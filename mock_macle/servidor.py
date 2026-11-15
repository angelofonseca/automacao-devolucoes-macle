"""Simulador do ERP MACLE Controle para demo e testes (dados fictícios de mock_macle/dados.json).

Serve as mesmas telas HTML que a automação lê no MACLE real (mesmos endereços, campos e funções JS),
só que geradas a partir de uma base fictícia:
  - login (empresa, terminal, usuário, senha) com cookie JSESSIONID; sem sessão -> redireciona ao login
  - consulta: filtro e lista de defeitos, defeito, item, histórico de compra, documento de compra
  - POST /_simular: falhas programadas (HTTP 503, atraso, sessões derrubadas) para mostrar a resiliência

Uso: python mock_macle/servidor.py [--porta 8080]   (endereço da base: http://127.0.0.1:8080/Controle/)
"""
import argparse
import copy
import html
import json
import os
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent
load_dotenv(RAIZ.parent / '.env')
BASE = json.loads((RAIZ / 'dados.json').read_text(encoding='utf-8'))

trava = threading.Lock()
estado = {}  # preenchido por reiniciar()


def reiniciar(usuario=None, senha=None):
    """Volta a base ao estado inicial (usado nos testes e ao subir o servidor)."""
    with trava:
        estado.clear()
        estado.update(defeitos={k: {**v, 'situacao': 'Pendente', 'local': 'Loja'} for k, v in BASE['defeitos'].items()},
                      sessoes={},
                      falha={'status': None, 'atraso': 0, 'vezes': 0},
                      usuario=usuario or os.environ.get('MACLE_USUARIO', 'demo'), senha=senha or os.environ.get('MACLE_SENHA', 'demo'))


reiniciar()
e = html.escape


def pagina(titulo, corpo):
    return f'<html><head><title>{e(titulo)}</title></head><body>{corpo}</body></html>'


def linhas_tabela(linhas):
    return '<table>' + ''.join('<tr>' + ''.join(f'<td>{c}</td>' for c in l) + '</tr>' for l in linhas) + '</table>'


def br(v, casas=2):
    return f'{v:,.{casas}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def compras_do_item(item):
    return [c for c in BASE['compras'] if item in c['itens']]


def compra(documento):
    return next(c for c in BASE['compras'] if c['documento'] == documento)


def fornecedor_do(documento):
    return documento.split('.')[1]


# ---------------------------------------------------------------- telas da consulta

def tela_login(erro=''):
    return pagina('MACLE Controle - Login', f'<form name="frmLogin" action="Login" method="post">{e(erro)}'
                  '<input name="empresa"><input name="terminal"><input name="usuario"><input type="password" name="senha"></form>')


def tela_filtro():
    empresas = ','.join(BASE['lojas'])
    return pagina('Filtro de defeitos', '<form name="frmFiltro" action="Defeito" method="post">'
                  '<input type="hidden" name="action" value="setFilterDefeito"><input name="codItem" value="">'
                  '<input name="dataCadIni" value=""><input name="dataCadFim" value=""><input type="checkbox" name="pendCli" checked>'
                  '<select name="situacao"><option value="0" selected>Todas</option></select></form>'
                  f'<input type="checkbox" id="empTodas" onclick="setEmpresas(\'{empresas}\')">')


def tela_lista_defeitos(sessao):
    f = sessao.get('filtro') or {}
    ini, fim = (''.join(reversed(f.get(k, '').split('/'))) for k in ('dataCadIni', 'dataCadFim'))
    linhas = []
    for cod, d in estado['defeitos'].items():
        emp, num = cod.split('.')
        cad = ''.join(reversed(d['cadastro'].split('/')))
        if f and (d['item'] != f.get('codItem') or emp not in f.get('empresas', '').split(',') or not ini <= cad <= fim):
            continue
        linhas.append([f'<a href="javascript:editDefeito({emp}, {num})">{cod}</a>', e(BASE['itens'][d['item']]['descricao']),
                       d['tamanho'], d['quantidade'], d['cadastro'], f"{d['local']} - {BASE['lojas'][emp]['nome']}" if d['local'] == 'Loja' else d['local'],
                       d['situacao']])
    return pagina('Defeitos', '<a href="Defeito?action=cons">Atualizar</a>' + linhas_tabela(linhas))


def tela_defeito(cod):
    d = estado['defeitos'].get(cod)
    if not d:
        return pagina('Defeito', 'Registro não encontrado')
    emp = cod.split('.')[0]
    local = f"{d['local']} - {BASE['lojas'][emp]['nome']}" if d['local'] == 'Loja' else d['local']
    return pagina('Defeito', linhas_tabela([
        ['Código', cod], ['Item', f"{d['item']} {e(BASE['itens'][d['item']]['descricao'])}"], ['Tamanho', d['tamanho']],
        ['Quantidade', d['quantidade']], ['Situação', d['situacao']], ['Local', local], ['Cadastro', d['cadastro']]])
        + f'<textarea name="obs">{e(d["obs"])}</textarea>')


def tela_item(item):
    it = BASE['itens'].get(item)
    if not it:
        return pagina('Item', 'Registro não encontrado')
    sel = lambda nome, v: f'<select name="{nome}"><option value="0">-- informe --</option><option value="1" selected>{e(v)}</option></select>'
    return pagina('Item', '<form name="frmItem">' + sel('codMarca', it['marca'])
                  + f'<input name="refFornecedor" value="{e(it["referencia"])}"><input name="descricao" value="{e(it["descricao"])}">'
                  + sel('codNivel2', it['tipo']) + sel('codNivel6', it['cor']) + sel('codNivel7', it['grade'])
                  + sel('codNivel8', BASE['fornecedores'][it['fornecedor']]['nome']) + '</form>')


def tela_historico(item):
    linhas = []
    for c in compras_do_item(item):
        loja, forn, serie, num = c['documento'].split('.')
        qtd = sum(c['itens'][item]['grade'].values())
        linhas.append([f"<a href=\"javascript:showDataDocCompra({loja}, {forn}, '{serie}', {num})\">{c['documento']}</a>",
                       c['movimento'], qtd, br(c['itens'][item]['custo']), br(qtd * c['itens'][item]['custo'])])
    return pagina('Histórico de compras', linhas_tabela([['Documento', 'Data', 'Qtd', 'Unit', 'Valor']] + linhas))


def tela_doc_compra(documento):
    try:
        c = compra(documento)
    except StopIteration:
        return pagina('Documento', 'Registro não encontrado')
    loja, forn = documento.split('.')[:2]
    f = BASE['fornecedores'][forn]
    corpo = linhas_tabela([['Documento', documento], ['Fornecedor', f"{forn} - {f['cnpj']} - {e(f['nome'])}"],
                           ['Data emiss.', c['emissao']], ['Data movto.', c['movimento']], ['Status', 'Confirmado'],
                           ['Transportadora', 'TRANSPORTES EXEMPLO']])
    corpo += f'<div><a href="javascript:showDataNFe({loja}, {c["codNFe"]})">NF-e</a></div>'
    for item, v in c['itens'].items():
        tams = sorted(v['grade'], key=int)
        corpo += linhas_tabela([[f"Item {item} - {e(BASE['itens'][item]['descricao'])}"],
                                tams + ['Qtd', 'Prc.Unit', 'Total'],
                                [v['grade'][t] for t in tams] + [sum(v['grade'].values()), br(v['custo']), br(v['custo'] * sum(v['grade'].values()))]])
    return pagina('Documento de compra', corpo)


# ---------------------------------------------------------------- servidor HTTP

class Handler(BaseHTTPRequestHandler):
    def responder(self, status=200, corpo='', tipo='text/html; charset=utf-8', location=None, cookie=None):
        dados = corpo if isinstance(corpo, bytes) else corpo.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', tipo)
        self.send_header('Content-Length', str(len(dados)))
        if location:
            self.send_header('Location', location)
        if cookie:
            self.send_header('Set-Cookie', f'JSESSIONID={cookie}; Path=/Controle; HttpOnly')
        self.end_headers()
        self.wfile.write(dados)

    def sessao(self):
        c = SimpleCookie(self.headers.get('Cookie') or '')
        token = c['JSESSIONID'].value if 'JSESSIONID' in c else None
        return token, estado['sessoes'].get(token)

    def falha_programada(self):
        with trava:
            f = estado['falha']
            if f['vezes'] <= 0:
                return False
            f['vezes'] -= 1
            status, atraso = f['status'], f['atraso']
        if atraso:
            time.sleep(atraso)
        if status:
            self.responder(status, pagina('Erro', f'HTTP {status} simulado'))
            return True
        return False

    def do_GET(self):
        self.tratar('GET', {})

    def do_POST(self):
        corpo = self.rfile.read(int(self.headers.get('Content-Length') or 0)).decode('utf-8')
        if urlparse(self.path).path == '/_simular':
            p = json.loads(corpo or '{}')
            with trava:
                if p.get('derrubarSessoes'):
                    estado['sessoes'].clear()
                estado['falha'].update(status=p.get('status'), atraso=p.get('atraso', 0), vezes=p.get('vezes', 0))
            return self.responder(200, json.dumps({'ok': True}), 'application/json')
        self.tratar('POST', {k: v[-1] for k, v in parse_qs(corpo, keep_blank_values=True).items()})

    def tratar(self, metodo, form):
        url = urlparse(self.path)
        if not url.path.startswith('/Controle'):
            return self.responder(404, 'não encontrado')
        if self.falha_programada():
            return
        rota = url.path[len('/Controle/'):] if url.path.startswith('/Controle/') else ''
        q = {k: v[-1] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
        token, sessao = self.sessao()
        if rota == '':
            novo = secrets.token_hex(16)
            with trava:
                estado['sessoes'][novo] = {'logado': False}
            return self.responder(200, tela_login(), cookie=novo)
        if rota == 'Login' and metodo == 'POST':
            if not sessao:
                return self.responder(302, location='/Controle/')
            ok = (form.get('action') == 'loggin' and form.get('empresa') in BASE['lojas']
                  and secrets.compare_digest(form.get('usuario', ''), estado['usuario'])
                  and secrets.compare_digest(form.get('senha', ''), estado['senha']))
            if not ok:
                return self.responder(200, tela_login('Usuário ou senha inválidos'))
            sessao.update(logado=True, empresa=form['empresa'], filtro=None)
            return self.responder(302, location='/Controle/Principal')
        if not sessao or not sessao.get('logado'):
            return self.responder(302, location='/Controle/')
        with trava:
            resposta = self.rota(metodo, rota, q, form, sessao)
        if isinstance(resposta, tuple):
            return self.responder(*resposta)
        self.responder(200, resposta)

    def rota(self, metodo, rota, q, f, s):
        acao = (f if metodo == 'POST' else q).get('action')
        # consulta
        if rota == 'Principal':
            return pagina('MACLE Controle', 'Menu')
        if rota == 'Defeito' and metodo == 'POST' and acao == 'setFilterDefeito':
            s['filtro'] = f
            return "<script>location.href='Defeito?action=cons'</script>"
        if rota == 'Defeito' and acao == 'cons':
            return tela_lista_defeitos(s)
        if rota == 'Defeito' and acao == 'cadFiltro':
            return tela_filtro()
        if rota == 'Defeito' and acao == 'edit':
            return tela_defeito(f"{q.get('codEmpresa')}.{q.get('codDefeito')}")
        if rota == 'Item' and acao == 'edit':
            return tela_item(f"{q.get('codEmpItem')}.{q.get('codItem')}")
        if rota == 'modEstoque/item/consHistoricoCompra.jsp':
            return tela_historico(f"{q.get('codEmpItem')}.{q.get('codItem')}")
        if rota == 'modCompras/docCompra/consDataDocCompra.jsp':
            return tela_doc_compra(f"{q.get('codEmpresa')}.{q.get('origem')}.{q.get('serie')}.{q.get('numero')}")
        return 404, pagina('Erro', f'rota não simulada: {rota} {acao}')

    def log_message(self, formato, *args):
        print(f'[macle-sim] {formato % args}', flush=True)


def main():
    p = argparse.ArgumentParser(description='Simulador do MACLE (dados fictícios)')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--porta', type=int, default=8080)
    a = p.parse_args()
    srv = ThreadingHTTPServer((a.host, a.porta), Handler)
    print(f'[macle-sim] http://{a.host}:{a.porta}/Controle/  (usuário {estado["usuario"]})', flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
