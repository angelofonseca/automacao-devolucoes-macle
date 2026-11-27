"""Uma rodada da automação: Gatilho -> Processamento -> Saída.

1. Lê os casos em data/entrada/ (JSON) e descarta os inválidos com motivo.
   Caso travado cujo motivo deixou de existir (ex.: o e-mail da fábrica foi cadastrado) destrava sozinho
   e volta para a triagem nesta mesma rodada.
2. Consulta os casos novos no MACLE (login + telas HTML). MACLE fora do ar não derruba a rodada:
   os casos ficam para a próxima e a falha vai no resumo.
3. Triagem (triagem.planejar): travar, aguardar lote ou montar o e-mail à fábrica.
4. Aplica o plano: rascunho no Gmail (IMAP) e mudança de status de cada caso.
5. Resumo no Telegram, só quando houve novidade ou falha.
"""
import json
from collections import Counter
from datetime import date, datetime

from . import casos as repo
from . import gmail, telegram, triagem
from .macle import consulta
from .macle.sessao import SessaoMacle
from .macle.telas import TelaDesconhecida
from .resiliencia import ErroIntegracao, registrar


def brl(valor):
    return f'R$ {valor:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def sessao_loja(cfg, ao_falhar=None):
    """Sessão no servidor do MACLE da loja desta instalação, logada com a empresa = loja."""
    loja = cfg.loja
    return SessaoMacle(loja['servidor'], loja['numero'], cfg.macle_terminal, cfg.macle_usuario, cfg.macle_senha,
                       timeout=cfg.timeout, tentativas=cfg.tentativas, ao_falhar=ao_falhar)


def executar(cfg, hoje=None, agora=None):
    hoje, agora = hoje or date.today(), agora or datetime.now()
    r = {'rascunhos': [], 'aguardando': [], 'bloqueados': [], 'destravados': [], 'invalidos': [], 'falhas': [], 'mudancas': 0}

    def log_tentativa(tentativa, erro):
        registrar(cfg.log, evento='falha_tentativa', servico=erro.servico, tentativa=tentativa,
                  detalhe=erro.detalhe, temporaria=erro.temporaria)

    cadastro = json.loads(cfg.fornecedores.read_text(encoding='utf-8'))

    # 1. Leitura e validação
    todos, pendentes, travados = [], [], []
    for pasta, caso, erros in repo.listar(cfg.entrada):
        if erros:
            r['invalidos'].append({'pasta': pasta.name, 'erros': erros})
            continue
        todos.append(caso)
        if caso['status'] in repo.EM_TRIAGEM:
            pendentes.append((pasta, caso))
        elif caso['status'] == 'aguardando_decisao':
            travados.append((pasta, caso))

    def mudar(pasta, caso, status, motivo, **extra):
        mudou = repo.mudar_status(caso, status, motivo)
        caso.update(extra)
        repo.gravar(pasta, caso)
        r['mudancas'] += mudou
        return mudou

    # Travados: confere de novo as regras da triagem. Se o motivo sumiu (cadastro corrigido), destrava sozinho.
    # Travado sem dados do ERP (defeito não achado no MACLE) ou que continua travado fica como está, sem novo aviso.
    for pasta, caso in travados:
        if 'erp' not in caso or triagem.avaliar(caso, cadastro['fornecedores'], hoje)['trava']:
            continue
        motivo = caso.pop('bloqueio', '')
        mudar(pasta, caso, 'novo', f'destravado: o motivo deixou de existir ({motivo})')
        r['destravados'].append({'protocolo': caso['protocolo'], 'motivo': motivo})
        pendentes.append((pasta, caso))

    # 2. Consulta no MACLE (uma sessão para a rodada inteira)
    sessao = None
    macle_fora = False
    prontos = {}
    for pasta, caso in pendentes:
        if 'erp' not in caso:
            if macle_fora:
                continue  # MACLE já falhou nesta rodada: não insiste caso a caso
            usados = Counter(c['erp']['codigoDefeito'] for c in todos if c.get('erp') and c is not caso)
            try:
                sessao = sessao or sessao_loja(cfg, log_tentativa)
                caso['erp'] = consulta.consultar(sessao, caso['codigo'], cfg.loja['numero'], hoje, usados)
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
    plano = triagem.planejar([c for _, c in prontos.values()], cadastro, cfg.loja, hoje, agora)

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
    for d in r['destravados']:
        linhas.append(f"🔓 Caso #{d['protocolo']} destravado (era: {d['motivo']}); voltou para a triagem")
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
