"""Uma rodada da automação: Gatilho -> Processamento -> Saída.

1. Lê os casos em data/entrada/ (JSON) e descarta os inválidos com motivo.
2. Enriquece os casos novos com o ERP (HTTP + login). ERP fora do ar não derruba a rodada:
   os casos ficam para a próxima e a falha vai no resumo.
3. Triagem (triagem.planejar) monta o plano: travar, aguardar lote ou montar rascunho.
4. Aplica o plano: rascunho no Gmail (IMAP) e mudança de status de cada caso.
5. Resumo no Telegram, só quando houve novidade ou falha.
"""
import json
from datetime import date, datetime

from . import casos as repo
from . import gmail, telegram, triagem
from .erp import ClienteERP, ProdutoInvalido
from .resiliencia import ErroIntegracao, registrar


def brl(valor):
    return f'R$ {valor:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def executar(cfg, hoje=None, agora=None, cliente_erp=None):
    hoje, agora = hoje or date.today(), agora or datetime.now()
    r = {'rascunhos': [], 'aguardando': [], 'bloqueados': [], 'invalidos': [], 'falhas': [], 'mudancas': 0}

    def log_tentativa(tentativa, erro):
        registrar(cfg.log, evento='falha_tentativa', servico=erro.servico, tentativa=tentativa,
                  detalhe=erro.detalhe, temporaria=erro.temporaria)

    cadastro = json.loads(cfg.fornecedores.read_text(encoding='utf-8'))
    erp = cliente_erp or ClienteERP(cfg, ao_falhar=log_tentativa)

    # 1. Leitura e validação
    pendentes = []
    for pasta, caso, erros in repo.listar(cfg.entrada):
        if erros:
            r['invalidos'].append({'pasta': pasta.name, 'erros': erros})
        elif caso['status'] in repo.EM_TRIAGEM:
            pendentes.append((pasta, caso))

    # 2. Enriquecimento com o ERP
    erp_fora = None
    prontos = {}
    for pasta, caso in pendentes:
        if 'erp' not in caso:
            if erp_fora:
                continue  # ERP já falhou nesta rodada: não insiste caso a caso
            try:
                caso['erp'] = erp.produto(caso['codigo'])
                repo.gravar(pasta, caso)
            except ProdutoInvalido as exc:
                if repo.mudar_status(caso, 'aguardando_decisao', str(exc)):
                    r['mudancas'] += 1
                caso['bloqueio'] = str(exc)
                repo.gravar(pasta, caso)
                r['bloqueados'].append({'protocolo': caso['protocolo'], 'motivo': str(exc)})
                continue
            except ErroIntegracao as exc:
                erp_fora = exc
                r['falhas'].append(f'{exc} (depois de {cfg.tentativas} tentativas)' if exc.temporaria else str(exc))
                continue
        prontos[caso['protocolo']] = (pasta, caso)
    if erp_fora:
        r['adiados'] = sum(1 for _, c in pendentes if 'erp' not in c)

    # 3. Triagem
    plano = triagem.planejar([c for _, c in prontos.values()], cadastro, hoje, agora)

    # 4. Aplicação do plano
    def mudar(protocolo, status, motivo, **extra):
        pasta, caso = prontos[protocolo]
        mudou = repo.mudar_status(caso, status, motivo)
        caso.update(extra)
        repo.gravar(pasta, caso)
        r['mudancas'] += mudou
        return mudou

    for b in plano['bloquear']:
        mudar(b['protocolo'], 'aguardando_decisao', b['motivo'], bloqueio=b['motivo'])
        r['bloqueados'].append(b)

    for a in plano['aguardarLote']:
        novos = [p for p in a['protocolos'] if mudar(p, 'aguardando_lote', f"lote {a['chave']} abaixo do mínimo")]
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
            mudar(p, 'aguardando_aprovacao', f"rascunho criado para {rasc['fornecedor']}",
                  rascunho={'messageId': criado['messageId'], 'para': rasc['para'], 'modo': criado['modo']})
        r['rascunhos'].append({**rasc, 'criado': criado})

    # 5. Saída: resumo no Telegram
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
