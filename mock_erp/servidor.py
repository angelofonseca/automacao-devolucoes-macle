"""ERP simulado para a demo (o ERP real só existe na rede da loja).

Imita o que a automação precisa do ERP: login com usuário/senha que devolve um cookie de sessão,
e a consulta de um produto (marca, custo e NF de compra) que só responde com esse cookie.

Uso:
  python mock_erp/servidor.py [--porta 8080]

Endpoints:
  POST /api/login            {"usuario": "...", "senha": "..."} -> 200 + Set-Cookie: sessao=...   | 401
  GET  /api/produtos/<código>                                     -> 200 JSON | 401 sem sessão | 404
  GET  /health                                                    -> 200 {"ok": true}
  POST /_simular             {"status": 503, "vezes": 2}          -> as próximas 2 chamadas falham com 503
                             {"atraso": 15, "vezes": 1}           -> a próxima chamada demora 15 s (timeout)
                             {"derrubarSessoes": true}            -> invalida as sessões (força novo login)
"""
import argparse
import json
import os
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent
PRODUTOS = json.loads((RAIZ / 'produtos.json').read_text(encoding='utf-8'))['produtos']

load_dotenv(RAIZ.parent / '.env')
USUARIO = os.environ.get('ERP_USUARIO', 'demo')
SENHA = os.environ.get('ERP_SENHA', 'demo')

sessoes = set()                                   # tokens de sessão válidos
falha = {'status': None, 'atraso': 0, 'vezes': 0}  # falha programada pelo /_simular
trava = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def responder(self, status, corpo, cookie=None):
        dados = json.dumps(corpo, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(dados)))
        if cookie:
            self.send_header('Set-Cookie', f'sessao={cookie}; HttpOnly; Path=/')
        self.end_headers()
        self.wfile.write(dados)

    def ler_json(self):
        tamanho = int(self.headers.get('Content-Length') or 0)
        try:
            return json.loads(self.rfile.read(tamanho) or b'{}')
        except json.JSONDecodeError:
            return None

    def falha_programada(self):
        """Aplica a falha do /_simular, se houver. Devolve True quando já respondeu com erro."""
        with trava:
            if falha['vezes'] <= 0:
                return False
            falha['vezes'] -= 1
            status, atraso = falha['status'], falha['atraso']
        if atraso:
            time.sleep(atraso)
        if status:
            self.responder(status, {'erro': f'falha simulada {status}'})
            return True
        return False

    def sessao_valida(self):
        cookie = SimpleCookie(self.headers.get('Cookie') or '')
        return 'sessao' in cookie and cookie['sessao'].value in sessoes

    def do_GET(self):
        caminho = urlparse(self.path).path
        if caminho == '/health':
            return self.responder(200, {'ok': True})
        if not caminho.startswith('/api/produtos/'):
            return self.responder(404, {'erro': 'rota inexistente'})
        if self.falha_programada():
            return
        if not self.sessao_valida():
            return self.responder(401, {'erro': 'sessão inválida ou expirada'})
        codigo = caminho.rsplit('/', 1)[-1]
        produto = PRODUTOS.get(codigo)
        if not produto:
            return self.responder(404, {'erro': f'produto {codigo} não encontrado'})
        self.responder(200, {'codigo': codigo, **produto})

    def do_POST(self):
        caminho = urlparse(self.path).path
        corpo = self.ler_json()
        if corpo is None:
            return self.responder(400, {'erro': 'JSON inválido'})
        if caminho == '/_simular':
            with trava:
                if corpo.get('derrubarSessoes'):
                    sessoes.clear()
                falha.update(status=corpo.get('status'), atraso=corpo.get('atraso', 0), vezes=corpo.get('vezes', 0))
            return self.responder(200, {'ok': True, 'falha': falha})
        if caminho != '/api/login':
            return self.responder(404, {'erro': 'rota inexistente'})
        if self.falha_programada():
            return
        # compare_digest: comparação em tempo constante, não vaza pelo tempo de resposta quantos caracteres batem
        if not (secrets.compare_digest(str(corpo.get('usuario', '')), USUARIO)
                and secrets.compare_digest(str(corpo.get('senha', '')), SENHA)):
            return self.responder(401, {'erro': 'usuário ou senha inválidos'})
        token = secrets.token_urlsafe(24)
        with trava:
            sessoes.add(token)
        self.responder(200, {'ok': True}, cookie=token)

    def log_message(self, formato, *args):
        print(f'[erp-mock] {self.address_string()} {formato % args}', flush=True)


def main():
    parser = argparse.ArgumentParser(description='ERP simulado da demo')
    parser.add_argument('--host', default=os.environ.get('MOCK_ERP_HOST', '127.0.0.1'))
    parser.add_argument('--porta', type=int, default=int(os.environ.get('MOCK_ERP_PORTA', 8080)))
    args = parser.parse_args()
    servidor = ThreadingHTTPServer((args.host, args.porta), Handler)
    print(f'[erp-mock] ouvindo em http://{args.host}:{args.porta} ({len(PRODUTOS)} produtos)', flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
