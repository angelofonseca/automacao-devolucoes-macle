"""Telegram Bot API (HTTPS, token do bot no .env): envio de mensagens e recepção das fotos dos casos."""
import requests

from .resiliencia import ErroIntegracao, checar_status, com_retry, esconder

SERVICO = 'Telegram'
LIMITE = 4096  # tamanho máximo de uma mensagem no Telegram


def enviar(cfg, texto, ao_falhar=None):
    texto = texto if len(texto) <= LIMITE else texto[:LIMITE - 20] + '\n… (cortado)'
    url = f'https://api.telegram.org/bot{cfg.telegram_token}/sendMessage'

    def tentar():
        try:
            r = requests.post(url, timeout=cfg.timeout, json={
                'chat_id': cfg.telegram_chat_id, 'text': texto, 'disable_web_page_preview': True})
        except requests.RequestException as exc:
            # a mensagem de erro do requests traz a URL, e a URL traz o token: limpa antes de seguir
            raise type(exc)(esconder(str(exc), cfg.telegram_token)) from None
        checar_status(r, SERVICO)
        corpo = r.json()
        if not corpo.get('ok'):  # a API pode responder 200 com ok=false
            raise ErroIntegracao(SERVICO, corpo.get('description', 'resposta sem ok'), temporaria=False)
        return {'modo': 'telegram', 'message_id': corpo['result']['message_id']}

    return com_retry(tentar, SERVICO, tentativas=cfg.tentativas, ao_falhar=ao_falhar)


def _api(cfg, metodo, espera=0, **params):
    """Chama um método da Bot API e devolve o 'result'. O token vai na URL, então sai de qualquer erro."""
    url = f'https://api.telegram.org/bot{cfg.telegram_token}/{metodo}'
    try:
        r = requests.post(url, json=params, timeout=cfg.timeout + espera)
    except requests.RequestException as exc:
        raise type(exc)(esconder(str(exc), cfg.telegram_token)) from None
    checar_status(r, SERVICO)
    corpo = r.json()
    if not corpo.get('ok'):
        raise ErroIntegracao(SERVICO, corpo.get('description', 'resposta sem ok'), temporaria=False)
    return corpo['result']


def receber(cfg, offset=None, espera=30):
    """Long polling (getUpdates): espera até `espera` s por mensagens novas.

    Passar offset = último update_id + 1 confirma ao Telegram as mensagens já tratadas (não voltam mais).
    """
    return com_retry(lambda: _api(cfg, 'getUpdates', espera, offset=offset, timeout=espera, allowed_updates=['message']),
                     SERVICO, tentativas=cfg.tentativas)


def baixar_arquivo(cfg, file_id, destino):
    """Baixa um arquivo enviado ao bot (a foto): getFile dá o caminho; o download é em /file/bot<token>/<caminho>."""
    def tentar():
        caminho = _api(cfg, 'getFile', file_id=file_id)['file_path']
        try:
            r = requests.get(f'https://api.telegram.org/file/bot{cfg.telegram_token}/{caminho}', timeout=cfg.timeout)
        except requests.RequestException as exc:
            raise type(exc)(esconder(str(exc), cfg.telegram_token)) from None
        checar_status(r, SERVICO)
        destino.write_bytes(r.content)
        return destino

    return com_retry(tentar, SERVICO, tentativas=cfg.tentativas)
