"""Rascunho no Gmail por IMAP (SSL, porta 993) com senha de app. Nunca envia: quem envia é a pessoa.

O Message-ID do rascunho é derivado dos protocolos do lote. Antes de gravar, a pasta de
rascunhos é consultada por esse Message-ID: rodar de novo não cria rascunho duplicado.
Sem senha de app no .env, o rascunho vira um arquivo .eml em saida/rascunhos/.
"""
import hashlib
import imaplib
import re
import time
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path

from .resiliencia import ErroIntegracao, com_retry

SERVICO = 'Gmail'
HOST, PORTA = 'imap.gmail.com', 993
TIPOS = {'.jpg': 'jpeg', '.jpeg': 'jpeg', '.png': 'png', '.webp': 'webp'}


def montar(cfg, rascunho, anexos=()):
    """EmailMessage a partir do item `montarRascunho` da triagem."""
    msg = EmailMessage()
    msg['From'] = cfg.gmail_user or 'automacao@localhost'
    msg['To'] = ', '.join(rascunho['para'])
    msg['Subject'] = rascunho['assunto']
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = message_id(rascunho)
    msg.set_content(rascunho['corpo'])
    for caminho in anexos:
        p = Path(caminho)
        msg.add_attachment(p.read_bytes(), maintype='image', subtype=TIPOS[p.suffix.lower()], filename=p.name)
    return msg


def message_id(rascunho):
    """Mesmo lote (mesmos protocolos) = mesmo Message-ID. É a chave da idempotência."""
    base = rascunho['chave'] + ':' + ','.join(sorted(rascunho['protocolos']))
    return f'<defeitos-{hashlib.sha1(base.encode()).hexdigest()[:16]}@automacao.local>'


def criar_rascunho(cfg, rascunho, anexos=(), ao_falhar=None):
    msg = montar(cfg, rascunho, anexos)
    if not cfg.gmail_ativo:
        pasta = cfg.saida / 'rascunhos'
        pasta.mkdir(parents=True, exist_ok=True)
        arquivo = pasta / f"{rascunho['chave']}.eml"
        arquivo.write_bytes(bytes(msg))
        return {'modo': 'arquivo', 'arquivo': str(arquivo), 'messageId': msg['Message-ID']}
    return com_retry(lambda: _gravar_imap(cfg, msg), SERVICO, tentativas=cfg.tentativas, ao_falhar=ao_falhar)


def _ok(resposta):
    status, dados = resposta
    if status != 'OK':
        raise ErroIntegracao(SERVICO, f'IMAP respondeu {status}', temporaria=False)
    return dados


def pasta_rascunhos(conn):
    """Nome da pasta de rascunhos pela marca \\Drafts (muda com o idioma da conta: Drafts, Rascunhos...)."""
    for linha in _ok(conn.list()):
        texto = linha.decode('utf-8', 'replace') if isinstance(linha, bytes) else ''
        if '\\Drafts' in texto:
            return re.search(r'"([^"]+)"\s*$', texto).group(1)
    raise ErroIntegracao(SERVICO, 'pasta de rascunhos não encontrada', temporaria=False)


def _gravar_imap(cfg, msg):
    try:
        conn = imaplib.IMAP4_SSL(HOST, PORTA, timeout=cfg.timeout)
    except OSError as exc:  # DNS, conexão recusada, timeout: temporário
        raise ErroIntegracao(SERVICO, f'sem conexão com {HOST}:{PORTA} ({type(exc).__name__})', temporaria=True)
    try:
        try:
            conn.login(cfg.gmail_user, cfg.gmail_senha)
        except imaplib.IMAP4.abort:
            raise  # conexão caiu: tratado abaixo como temporário
        except imaplib.IMAP4.error:
            raise ErroIntegracao(SERVICO, 'login recusado: confira GMAIL_USER e GMAIL_APP_PASSWORD', temporaria=False)
        pasta = pasta_rascunhos(conn)
        mid = msg['Message-ID']
        _ok(conn.select(f'"{pasta}"'))
        ja_existe = _ok(conn.search(None, 'HEADER', 'Message-ID', mid))[0].split()
        if ja_existe:
            return {'modo': 'gmail', 'messageId': mid, 'jaExistia': True}
        _ok(conn.append(f'"{pasta}"', '(\\Draft)', imaplib.Time2Internaldate(time.time()), bytes(msg)))
        return {'modo': 'gmail', 'messageId': mid, 'jaExistia': False}
    except imaplib.IMAP4.abort as exc:  # conexão caiu no meio: temporário
        raise ErroIntegracao(SERVICO, f'conexão IMAP interrompida ({exc})', temporaria=True)
    except OSError as exc:
        raise ErroIntegracao(SERVICO, f'falha de rede no IMAP ({type(exc).__name__})', temporaria=True)
    finally:
        try:
            conn.logout()
        except Exception:
            pass
