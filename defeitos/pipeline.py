"""Uma rodada da automação: Gatilho -> Processamento -> Saída.

1. Lê os casos em data/entrada/ (JSON) e descarta os inválidos com motivo.
2. Consulta os casos novos no MACLE (login + telas HTML). MACLE fora do ar não derruba a rodada:
   os casos ficam para a próxima e a falha vai no resumo.
3. Triagem (triagem.planejar): travar, aguardar lote ou montar o e-mail à fábrica.
4. Aplica o plano: rascunho no Gmail (IMAP) e mudança de status de cada caso.
5. NFD: casos que a fábrica autorizou viram rascunho da nota de devolução no MACLE (loja da NF de compra).
6. Resumo no Telegram, só quando houve novidade ou falha.
"""
import json
from datetime import date, datetime

from . import casos as repo
from . import gmail, telegram, triagem
from .macle import consulta, nfd
from .macle.sessao import SessaoMacle
from .macle.telas import TelaDesconhecida
from .resiliencia import ErroIntegracao, registrar


def brl(valor):
    return f'R$ {valor:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def sessao_loja(cfg, loja, ao_falhar=None):
    """Sessão no servidor do MACLE da loja, logada com a empresa = loja."""
    return SessaoMacle(cfg.loja(loja)['servidor'], loja, cfg.macle_terminal, cfg.macle_usuario, cfg.macle_senha,
                       timeout=cfg.timeout, tentativas=cfg.tentativas, ao_falhar=ao_falhar)


def executar(cfg, hoje=None, agora=None):
    hoje, agora = hoje or date.today(), agora or datetime.now()
    r = {'rascunhos': [], 'aguardando': [], 'bloqueados': [], 'invalidos': [], 'nfds': [], 'falhas': [], 'mudancas': 0}

    def log_tentativa(tentativa, erro):
        registrar(cfg.log, evento='falha_tentativa', servico=erro.servico, tentativa=tentativa,
                  detalhe=erro.detalhe, temporaria=erro.temporaria)

    cadastro = json.loads(cfg.fornecedores.read_text(encoding='utf-8'))

    # 1. Leitura e validação
    todos, pendentes, autorizados = [], [], []
    for pasta, caso, erros in repo.listar(cfg.entrada):
        if erros:
            r['invalidos'].append({'pasta': pasta.name, 'erros': erros})
            continue
        todos.append(caso)
        if caso['status'] in repo.EM_TRIAGEM:
            pendentes.append((pasta, caso))
        elif caso['status'] == 'autorizado':
            autorizados.append((pasta, caso))

    def mudar(pasta, caso, status, motivo, **extra):
        mudou = repo.mudar_status(caso, status, motivo)
        caso.update(extra)
        repo.gravar(pasta, caso)
        r['mudancas'] += mudou
        return mudou

    # 2. Consulta no MACLE (uma sessão para a rodada inteira)
    sessao = None
    macle_fora = False
    prontos = {}
    for pasta, caso in pendentes:
        if 'erp' not in caso:
            if macle_fora:
                continue  # MACLE já falhou nesta rodada: não insiste caso a caso
            usados = {c['erp']['codigoDefeito'] for c in todos if c.get('erp') and c is not caso}
            try:
                sessao = sessao or sessao_loja(cfg, cfg.lojas['consulta'], log_tentativa)
                caso['erp'] = consulta.consultar(sessao, caso['codigo'], cfg.lojas['ordemLojas'], hoje, usados)
                caso['quantidade'] = caso['erp']['quantidade']  # o MACLE é a fonte da quantidade do defeito
                repo.gravar(pasta, caso)
            except (consulta.ProdutoNaoEncontrado, TelaDesconhecida) as exc:
                mudar(pasta, caso, 'aguardando_decisao', str(exc), bloqueio=str(exc))
                r['bloqueados'].append({'protocolo': caso['protocolo'], 'motivo': str(exc)})
                continue
            except ErroIntegracao as exc:
                macle_fora = True
                r['falhas'].append(f'{exc} (depois de {cfg.tentativas} tentativas)' if exc.temporaria else str(exc))
                continue
        prontos[caso['protocolo']] = (pasta, caso)
    if macle_fora:
        r['adiados'] = sum(1 for _, c in pendentes if 'erp' not in c)

    # 3. Triagem
    plano = triagem.planejar([c for _, c in prontos.values()], cadastro, cfg.lojas['lojas'], hoje, agora)

    # 4. Aplicação do plano
    for b in plano['bloquear']:
        mudar(*prontos[b['protocolo']], 'aguardando_decisao', b['motivo'], bloqueio=b['motivo'])
        r['bloqueados'].append(b)
    for a in plano['aguardarLote']:
        novos = [p for p in a['protocolos'] if mudar(*prontos[p], 'aguardando_lote', f"lote {a['chave']} abaixo do mínimo")]
        r['aguardando'].append({**a, 'novos': novos})
    for rasc in plano['montarRascunho']:
        anexos = [prontos[i['protocolo']][0] / foto for i in rasc['itens'] for foto in i['fotos']
                  if (prontos[i['protocolo']][0] / foto).is_file()]
        try:
            criado = gmail.criar_rascunho(cfg, rasc, anexos, ao_falhar=log_tentativa)
        except ErroIntegracao as exc:
            r['falhas'].append(f"{exc} (rascunho {rasc['chave']} fica para a próxima rodada)")
            continue
        for p in rasc['protocolos']:
            mudar(*prontos[p], 'aguardando_aprovacao', f"rascunho criado para {rasc['fornecedor']}",
                  rascunho={'messageId': criado['messageId'], 'para': rasc['para'], 'modo': criado['modo']})
        r['rascunhos'].append({**rasc, 'criado': criado})

    # 5. NFD dos casos autorizados pela fábrica: uma por loja da NF + fornecedor
    grupos = {}
    for pasta, caso in autorizados:
        nf = caso['erp']['nf']
        grupos.setdefault((nf['loja'], triagem.so_digitos(nf['cnpjFornecedor'])), []).append((pasta, caso))
    for (loja, cnpj), membros in grupos.items():
        f = triagem.fornecedor_por_cnpj(cnpj, cadastro['fornecedores']) or {}
        g = {'loja': loja, 'cnpjLoja': cfg.loja(loja)['cnpj'], 'cnpjFornecedor': membros[0][1]['erp']['nf']['cnpjFornecedor'],
             'fornecedor': membros[0][1]['erp']['nf']['fornecedor'], 'transportadora': f.get('transportadora'),
             'casos': [c for _, c in membros]}
        protocolos = [c['protocolo'] for _, c in membros]
        try:
            res = nfd.montar_rascunho(g, sessao_loja(cfg, loja, log_tentativa), cfg.peso_par, cfg.peso_caixa)
        except nfd.Parada as exc:
            for pasta, caso in membros:
                mudar(pasta, caso, 'aguardando_decisao', f'NFD: {exc}', bloqueio=f'NFD: {exc}')
            r['bloqueados'] += [{'protocolo': p, 'motivo': f'NFD: {exc}'} for p in protocolos]
            continue
        except ErroIntegracao as exc:
            r['falhas'].append(f'{exc} (NFD da loja {loja} fica para a próxima rodada)')
            continue
        arquivo = cfg.saida / 'nfd' / f"L{loja}-{cnpj}-{'-'.join(protocolos)}.pdf"
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_bytes(res['danfe'])
        for pasta, caso in membros:
            mudar(pasta, caso, 'nfd_rascunho', 'rascunho da NFD salvo no MACLE (Continuar depois)',
                  nfd={'loja': loja, 'valor': res['valor'], 'danfe': str(arquivo), 'em': agora.isoformat(timespec='seconds')})
        r['nfds'].append({'fornecedor': f.get('nome') or g['fornecedor'], 'loja': loja, 'valor': res['valor'],
                          'protocolos': protocolos, 'avisos': res['avisos'], 'danfe': str(arquivo)})

    # 6. Saída: resumo no Telegram
    r['mensagem'] = mensagem(r, agora)
    r['avisou'] = False
    if r['mudancas'] or r['falhas'] or r['invalidos']:
        try:
            r['telegram'] = telegram.enviar(cfg, r['mensagem'], ao_falhar=log_tentativa)
            r['avisou'] = True
        except ErroIntegracao as exc:
            r['falhas'].append(str(exc))  # fica no log; não há outro canal para avisar
    r['ok'] = not r['falhas']
    return r


def mensagem(r, agora):
    """Texto do resumo da rodada (o que a pessoa lê no celular)."""
    linhas = [f'📦 Defeitos — rodada de {agora:%d/%m %H:%M}']
    for x in r['rascunhos']:
        linhas.append(f"✉️ Rascunho pronto para revisar: {x['fornecedor']} (loja {x['loja']}), "
                      f"{x['pares']} {'par' if x['pares'] == 1 else 'pares'}, {brl(x['total'])}, casos #{', #'.join(x['protocolos'])}")
        linhas += [f'   ⚠️ {a}' for a in x['avisos']]
    for n in r['nfds']:
        linhas.append(f"🧾 NFD em rascunho no MACLE: {n['fornecedor']} (loja {n['loja']}), {brl(n['valor'] or 0)}, "
                      f"casos #{', #'.join(n['protocolos'])}. Conferir e emitir na tela.")
        linhas += [f'   ⚠️ {a}' for a in n['avisos']]
    for a in r['aguardando']:
        falta = []
        if a['total'] < a['minimo']:
            falta.append(f"faltam {brl(a['falta'])} (mínimo {brl(a['minimo'])})")
        if a['pares'] < a['minimoPares']:
            falta.append(f"{a['pares']} de {a['minimoPares']} pares")
        linhas.append(f"⏳ Lote {a['fornecedor']} (loja {a['loja']}): {brl(a['total'])}, {'; '.join(falta)}")
    for b in r['bloqueados']:
        linhas.append(f"⛔ Caso #{b['protocolo']} precisa de decisão: {b['motivo']}")
    for i in r['invalidos']:
        linhas.append(f"❓ Pasta {i['pasta']} com caso inválido: {'; '.join(i['erros'])}")
    for f in r['falhas']:
        linhas.append(f'🚨 Falha: {f}')
    if r.get('adiados'):
        linhas.append(f"   {r['adiados']} caso(s) ficam para a próxima rodada.")
    if len(linhas) == 1:
        linhas.append('Nada novo.')
    return '\n'.join(linhas)
