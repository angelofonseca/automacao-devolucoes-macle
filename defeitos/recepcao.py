"""Recepção pelo Telegram (gatilho): o gerente manda a foto do par com o código na legenda.

  python -m defeitos.recepcao      fica ouvindo o bot (long polling); Ctrl+C para parar

Para cada foto que chega do chat autorizado (TELEGRAM_CHAT_ID):
  1. lê o código na legenda: item com tamanho (ex.: 1.1001.38) ou o do defeito no MACLE (ex.: 1.60000006);
  2. consulta o MACLE: precisa haver um defeito em aberto desse item e tamanho, cadastrado pela loja;
  3. registra o caso com a foto e os dados do ERP (loja, quantidade, descrição do defeito);
  4. roda uma rodada da automação: triagem, rascunho do e-mail se o lote fechou e resumo no Telegram.
Mensagens de qualquer outro chat são ignoradas.
"""
import argparse
import sys
import tempfile
import time
from pathlib import Path

from . import casos as repo
from . import config, novo_caso, pipeline, telegram
from .macle import consulta
from .macle.telas import TelaDesconhecida
from .main import rodar
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
    usados = {c['erp']['codigoDefeito'] for _, c, erros in repo.listar(cfg.entrada) if not erros and c.get('erp')}
    sessao = pipeline.sessao_loja(cfg, cfg.lojas['consulta'])
    erp = consulta.consultar(sessao, codigo, cfg.lojas['ordemLojas'], hoje, ignorar=usados)
    loja = erp['codigoDefeito'].split('.')[0]  # o defeito fica na empresa (loja) que recebeu o par
    with tempfile.TemporaryDirectory() as tmp:
        foto = telegram.baixar_arquivo(cfg, file_id, Path(tmp) / f'foto{ext}')
        caso = novo_caso.criar(cfg.entrada, loja, codigo, erp['quantidade'],
                               descricao or erp['observacao'] or 'ver foto', foto)
    caso['erp'] = erp  # a rodada vê o 'erp' e não consulta o MACLE de novo
    repo.gravar(cfg.entrada / caso['protocolo'], caso)
    return caso


def atender(cfg, msg):
    """Trata uma mensagem do chat autorizado: registra o caso e roda a automação, ou responde o que faltou."""
    lido = ler_mensagem(msg)
    if not lido or not lido[0]:
        telegram.enviar(cfg, AJUDA)
        return
    codigo, descricao, file_id, ext = lido
    print(f'Recebido: {codigo}')
    try:
        caso = registrar_foto(cfg, codigo, descricao, file_id, ext)
    except (consulta.ProdutoNaoEncontrado, TelaDesconhecida) as exc:
        telegram.enviar(cfg, f'⚠️ {codigo}: {exc}.\nConfira o código ou cadastre o defeito no MACLE e mande de novo.')
        return
    except (ErroIntegracao, ValueError) as exc:
        telegram.enviar(cfg, f'🚨 Não consegui registrar {codigo}: {esconder(str(exc), *cfg.segredos)}. Tente de novo em instantes.')
        return
    e = caso['erp']
    registrar(cfg.log, evento='recepcao', protocolo=caso['protocolo'], codigo=codigo, defeito=e['codigoDefeito'])
    telegram.enviar(cfg, f"✅ Caso #{caso['protocolo']} registrado: {e['marca']} {e['referencia']}, tam. {e['tamanho']}, "
                         f"loja {caso['loja']}\nDefeito {e['codigoDefeito']} no MACLE: {caso['defeito']}")
    rodar(cfg)  # triagem, rascunho do e-mail se o lote fechou e resumo no Telegram


def main(argv=None):
    for fluxo in (sys.stdout, sys.stderr):
        fluxo.reconfigure(encoding='utf-8')
    argparse.ArgumentParser(description='Recepção de defeitos pelo Telegram').parse_args(argv)
    try:
        cfg = config.carregar()
    except config.ConfigInvalida as exc:
        print(f'[CONFIG] {exc}', file=sys.stderr)
        return 2
    if not cfg.telegram_ativo:
        print('[CONFIG] a recepção precisa de TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env', file=sys.stderr)
        return 2

    # O offset guarda até onde já tratamos: reiniciar a recepção não registra a mesma foto duas vezes.
    arq_offset = cfg.saida / 'telegram_offset.txt'
    offset = int(arq_offset.read_text()) if arq_offset.exists() else None
    print('Recepção ativa: mande a foto com o código na legenda para o bot (Ctrl+C para parar)')
    try:
        while True:
            try:
                atualizacoes = telegram.receber(cfg, offset)
            except ErroIntegracao as exc:  # Telegram fora do ar: registra, espera e tenta de novo
                registrar(cfg.log, evento='recepcao', ok=False, erro=str(exc))
                print(f'[ERRO] {exc}; nova tentativa em 30 s', file=sys.stderr)
                time.sleep(30)
                continue
            for u in atualizacoes:
                offset = u['update_id'] + 1
                arq_offset.parent.mkdir(parents=True, exist_ok=True)
                arq_offset.write_text(str(offset))  # antes de atender: uma mensagem que quebra não volta em loop
                msg = u.get('message') or {}
                if str(msg.get('chat', {}).get('id')) != cfg.telegram_chat_id:
                    continue  # só o chat autorizado registra defeitos
                try:
                    atender(cfg, msg)
                except Exception as exc:  # nunca derruba a recepção: registra e avisa
                    detalhe = esconder(f'{type(exc).__name__}: {exc}', *cfg.segredos)
                    registrar(cfg.log, evento='recepcao', ok=False, erro=detalhe)
                    print(f'[ERRO] {detalhe}', file=sys.stderr)
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
