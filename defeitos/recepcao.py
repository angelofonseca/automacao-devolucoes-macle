"""Recepção pelo Telegram (gatilho): o gerente manda a foto do par com o código na legenda.

Quem ouve o bot é o defeitos.main. Para cada foto que chega do chat autorizado (TELEGRAM_CHAT_ID):
  1. lê o código na legenda: item com tamanho (ex.: 1.1001.38) ou o do defeito no MACLE (ex.: 1.60000006);
  2. consulta o MACLE: precisa haver um defeito em aberto desse item e tamanho, cadastrado pela loja
     desta instalação (código de outra loja é recusado);
  3. registra o caso (um par) com a foto e os dados do ERP; um defeito com quantidade N no MACLE
     recebe N fotos, uma por par, cada uma com a sua descrição;
  4. responde no chat; o main roda em seguida uma rodada (triagem, rascunho se o lote fechou, resumo).
"""
import tempfile
from collections import Counter
from pathlib import Path

from . import casos as repo
from . import pipeline, telegram
from .macle import consulta
from .macle.telas import TelaDesconhecida
from .resiliencia import ErroIntegracao, esconder, registrar

AJUDA = ('Para registrar um defeito, mande a foto do par com o código na legenda:\n'
         '• item com tamanho, ex.: 1.1001.38\n'
         '• ou o código do defeito no MACLE, ex.: 1.60000006\n'
         'Depois do código, se quiser, a descrição do defeito.')


def ler_mensagem(msg):
    """(codigo, descricao, file_id, extensao) de uma mensagem com foto; None se não vier foto."""
    if msg.get('photo'):
        file_id, ext = msg['photo'][-1]['file_id'], '.jpg'  # o Telegram manda várias resoluções: a última é a maior
    elif (msg.get('document') or {}).get('mime_type', '').startswith('image/'):  # foto enviada "como arquivo"
        doc = msg['document']
        file_id, ext = doc['file_id'], Path(doc.get('file_name') or 'foto.jpg').suffix.lower()
    else:
        return None
    partes = (msg.get('caption') or '').split(maxsplit=1)
    codigo = partes[0] if partes else ''
    descricao = partes[1].strip() if len(partes) > 1 else ''
    return codigo, descricao, file_id, ext


def registrar_foto(cfg, codigo, descricao, file_id, ext, hoje=None):
    """Consulta o defeito no MACLE, baixa a foto e grava o caso (já com os dados do ERP).

    Levanta consulta.ProdutoNaoEncontrado quando não há defeito em aberto para o código.
    """
    usados = Counter(c['erp']['codigoDefeito'] for _, c, erros in repo.listar(cfg.entrada) if not erros and c.get('erp'))
    sessao = pipeline.sessao_loja(cfg)
    loja = cfg.loja['numero']  # cada instalação é de uma loja: só os defeitos dela
    erp = consulta.consultar(sessao, codigo, loja, hoje, usados)
    with tempfile.TemporaryDirectory() as tmp:
        foto = telegram.baixar_arquivo(cfg, file_id, Path(tmp) / f'foto{ext}')
        caso = repo.criar(cfg.entrada, loja, codigo, descricao or erp['observacao'] or 'ver foto', foto)
    caso['erp'] = erp  # a rodada vê o 'erp' e não consulta o MACLE de novo
    repo.gravar(cfg.entrada / caso['protocolo'], caso)
    return caso


def atender(cfg, msg):
    """Trata uma mensagem do chat autorizado. Devolve o caso registrado, ou None (e responde o que faltou)."""
    lido = ler_mensagem(msg)
    if not lido or not lido[0]:
        telegram.enviar(cfg, AJUDA)
        return None
    codigo, descricao, file_id, ext = lido
    print(f'Recebido: {codigo}')
    try:
        caso = registrar_foto(cfg, codigo, descricao, file_id, ext)
    except (consulta.ProdutoNaoEncontrado, TelaDesconhecida) as exc:
        telegram.enviar(cfg, f'⚠️ {codigo}: {exc}.\nConfira o código ou cadastre o defeito no MACLE e mande de novo.')
        return None
    except (ErroIntegracao, ValueError) as exc:
        telegram.enviar(cfg, f'🚨 Não consegui registrar {codigo}: {esconder(str(exc), *cfg.segredos)}. Tente de novo em instantes.')
        return None
    e = caso['erp']
    registrar(cfg.log, evento='recepcao', protocolo=caso['protocolo'], codigo=codigo, defeito=e['codigoDefeito'])
    telegram.enviar(cfg, f"✅ Caso #{caso['protocolo']} registrado: {e['marca']} {e['referencia']}, tam. {e['tamanho']}, "
                         f"loja {caso['loja']}\nDefeito {e['codigoDefeito']} no MACLE"
                         + (f" (par {e['par']} de {e['pares']})" if e.get('pares', 1) > 1 else '') + f": {caso['defeito']}")
    return caso
