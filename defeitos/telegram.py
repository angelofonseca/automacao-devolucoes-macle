"""Envio de mensagem pelo Telegram Bot API (HTTPS, token do bot no .env).

Sem token configurado, a mensagem vai para saida/telegram.log (útil para testar sem bot).
"""
from datetime import datetime

import requests

from .resiliencia import ErroIntegracao, checar_status, com_retry, esconder

SERVICO = 'Telegram'
LIMITE = 4096  # tamanho máximo de uma mensagem no Telegram


def enviar(cfg, texto, ao_falhar=None):
    texto = texto if len(texto) <= LIMITE else texto[:LIMITE - 20] + '\n… (cortado)'
    if not cfg.telegram_ativo:
        arquivo = cfg.saida / 'telegram.log'
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        with open(arquivo, 'a', encoding='utf-8') as f:
            f.write(f'--- {datetime.now():%d/%m/%Y %H:%M:%S}\n{texto}\n')
        return {'modo': 'arquivo', 'arquivo': str(arquivo)}

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
