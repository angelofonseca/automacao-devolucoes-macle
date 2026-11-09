"""Simulador do ERP MACLE Controle para demo e testes (dados fictícios de mock_macle/dados.json).

Serve as mesmas telas HTML que a automação lê no MACLE real (mesmos endereços, campos e funções JS),
só que geradas a partir de uma base fictícia:
  - login (empresa, terminal, usuário, senha) com cookie JSESSIONID; sem sessão -> redireciona ao login
  - consulta: filtro e lista de defeitos, defeito, item, histórico de compra, documento de compra
  - devolução ao fornecedor (NFD) até "Continuar depois", com a prévia do DANFE em PDF
  - POST /_simular: falhas programadas (HTTP 503, atraso, sessões derrubadas) para mostrar a resiliência

Uso: python mock_macle/servidor.py [--porta 8080]   (endereço da base: http://127.0.0.1:8080/Controle/)
"""
import argparse
import copy
import html
import itertools
import json
import os
import re
import secrets
import threading
import time
import zlib
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
                      sessoes={}, pendentes=[], pdfs={}, seq=itertools.count(1000),
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


def descricao_defeito(cod, d):
    return f"{d['item']}.{d['tamanho']} {BASE['itens'][d['item']]['descricao']}"


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


# ---------------------------------------------------------------- telas da devolução (NFD)

def tela_lista_devolucoes(sessao):
    n = sum(1 for p in estado['pendentes'] if p['loja'] == sessao['empresa'])
    return pagina('Devolução ao fornecedor', f'Existe(m) {n} documento(s) para continuar a devolução')


def tela_pendentes(sessao):
    linhas = [['Tipo', 'Fornecedor', 'Produtos', 'Qtd. Total']]
    linhas += [['Devolução', e(p['fornecedor']), p['produtos'], br(p['qtd'])] for p in estado['pendentes'] if p['loja'] == sessao['empresa']]
    return pagina('Continuar devolução', linhas_tabela(linhas))


def form_principal(acao, extra=''):
    return (f'<form name="frmLiquidaReceber" method="post" action="DevolFornecedor"><input type="hidden" name="action" value="{acao}">'
            f'<input type="hidden" name="resY" value="897">{extra}</form>')


def defeitos_livres(sessao, forn):
    """Defeitos pendentes na loja da sessão cujo item é do fornecedor e que ainda não estão no documento."""
    doc = sessao['doc']
    usados = {i['defeito'] for i in doc['itens']}
    return [cod for cod, d in estado['defeitos'].items() if cod.split('.')[0] == sessao['empresa'] and d['situacao'] == 'Pendente'
            and BASE['itens'][d['item']]['fornecedor'] == forn and cod not in usados]


def tela_defeitos_fornecedor(sessao):
    linhas = [[f'<input type="checkbox" name="chk_{cod.replace(".", "_")}">', cod, descricao_defeito(cod, estado['defeitos'][cod])]
              for cod in defeitos_livres(sessao, sessao['doc']['forn'])]
    return pagina('Devolução - defeitos', linhas_tabela(linhas))


def tela_item_origem(sessao, pend):
    """Selecione o item de origem: o item sendo devolvido e as linhas do XML da NF de compra."""
    c = compra(pend['documento'])
    loja, forn, serie, num = pend['documento'].split('.')
    emp, cod = pend['item'].split('.')
    base = (f"DevolFornecedor?action=setNItemOrigem&nitem=' + nitem + '&codItem={cod}&codTamanho={pend['tamanho']}&chave={c['chave']}"
            f"&codEmpOrig={loja}&origemOrig={forn}&serieOrig={serie}&numeroOrig={num}")
    confirmar = f"DevolFornecedor?action=confirmaResolveNItem&chave={c['chave']}&codEmpOrig={loja}&origemOrig={forn}&serieOrig={serie}&numeroOrig={num}"
    linhas = [[f'<a href="javascript:associar({l["nitem"]})">{l["nitem"]}</a>', l['codigo'], e(l['descricao']), l['qtd']] for l in c['linhasXml']]
    return pagina('Item de origem', f"Devolvendo agora:<br>{pend['item']}.{pend['tamanho']} - {e(BASE['itens'][pend['item']]['descricao'])}<br>"
                  + linhas_tabela(linhas)
                  + f"<script>function associar(nitem) {{ window.location.href = '{base}'; }}\n"
                  + f"function confirmar() {{ window.location.href = '{confirmar}'; }}</script>")


def tela_fechamento(sessao):
    doc = sessao['doc']
    nfs = sorted({i['documento'].split('.')[-1] for i in doc['itens']})
    itens = linhas_tabela([[f"{i['item']}.{i['tamanho']}", i['qtd']] for i in doc['itens']])
    obs = f"Devolução de mercadoria com defeito de fabricação. NF de origem: {', '.join(nfs)}."
    return pagina('Devolução - fechamento', 'Pronto para finalizar' + itens + form_principal(
        'fechamto', f'<input name="infAdicional" value=""><textarea name="obs">{e(obs)}</textarea>')
        + "<script>function dadosTransporte() { var resY = 897; "
          "window.open('modCaixa/cadDadosTransporte.jsp?codEmpresa=null&origem=0&serie=null&numero=null&resY=' + resY + '&tipoDoc=4'); }</script>")


def tela_transporte():
    opcoes = ''.join(f'<option value="{t["codigo"]}">{e(t["nome"])} - {t["cnpj"]}</option>' for t in BASE['transportadoras'])
    campos = ''.join(f'<input name="{n}" value="">' for n in ('codTransportadora', 'transportadora', 'cnpjTransp', 'inscEstTransp',
                                                               'endereco', 'bairro', 'cidade', 'codUF', 'codCEP', 'especie',
                                                               'quantidade', 'pesoLiquido', 'pesoBruto'))
    return pagina('Dados do transporte', '<form name="frmTransp" action="DadosTransp" method="post">'
                  '<input type="hidden" name="action" value=""><input type="hidden" name="tipoDoc" value="4">'
                  f'<select name="selectTransp"><option value="0">-- selecione --</option>{opcoes}</select>'
                  f'<input name="fretePorConta" value="0">{campos}</form>')


def gerar_pdf(linhas):
    """PDF de uma página com as linhas em Helvetica (stream comprimido com Flate, como o DANFE do MACLE)."""
    esc = lambda s: s.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
    conteudo = 'BT /F1 9 Tf 40 800 Td ' + ' '.join(f'({esc(l)}) Tj 0 -11 Td' for l in linhas) + ' ET'
    stream = zlib.compress(conteudo.encode('latin-1', 'replace'))
    objs = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>',
            b'<< /Length %d /Filter /FlateDecode >>\nstream\n' % len(stream) + stream + b'\nendstream']
    pdf, offs = bytearray(b'%PDF-1.4\n'), []
    for i, o in enumerate(objs, 1):
        offs.append(len(pdf))
        pdf += b'%d 0 obj\n' % i + o + b'\nendobj\n'
    xref = len(pdf)
    pdf += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objs) + 1) + b''.join(b'%010d 00000 n \n' % o for o in offs)
    pdf += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objs) + 1, xref)
    return bytes(pdf)


def danfe(sessao):
    doc = sessao['doc']
    loja, forn = BASE['lojas'][sessao['empresa']], BASE['fornecedores'][doc['forn']]
    l = ['DANFE - PREVIA', 'EMITENTE', loja['razao'], loja['cnpj'], 'DESTINATARIO', forn['nome'], forn['cnpj'], 'CFOP', '6202']
    total, agrupado = 0, {}
    for i in doc['itens']:
        chave = f"{i['item']}.{i['tamanho']}"
        agrupado[chave] = agrupado.get(chave, 0) + i['qtd']
        total += i['qtd'] * compra(i['documento'])['itens'][i['item']]['custo']
    for chave, qtd in agrupado.items():
        item = chave.rsplit('.', 1)[0]
        l += [chave, BASE['itens'][item]['descricao'], 'PAR', br(qtd, 4)]
    l += ['VALOR TOTAL DOS PRODUTOS', br(total), 'VALOR DA NOTA', br(total)]
    if doc.get('transporte'):
        t = doc['transporte']
        l += ['TRANSPORTADOR', t['transportadora'], t['cnpjTransp'], 'PESO LIQUIDO', t['pesoLiquido'], 'PESO BRUTO', t['pesoBruto']]
    return gerar_pdf(l)


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
            sessao.update(logado=True, empresa=form['empresa'], doc=None, filtro=None)
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
        doc = s.get('doc')
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
        # devolução ao fornecedor
        if rota == 'DevolFornecedor' and metodo == 'GET' and acao == 'cons':
            return tela_lista_devolucoes(s)
        if rota == 'modEstoque/devolFornecedor/continuaDevolucao.jsp':
            return tela_pendentes(s)
        if rota == 'DevolFornecedor' and acao == 'add' and metodo == 'GET':
            return pagina('Nova devolução', form_principal('fechamto' if doc else 'add'))
        if rota == 'DevolFornecedor' and acao == 'add' and metodo == 'POST':
            s['doc'] = {'forn': None, 'itens': [], 'selecionado': None, 'nf': None, 'transporte': None}
            return pagina('Nova devolução', form_principal('origem'))
        if not doc:
            return 500, pagina('Erro', 'java.lang.NullPointerException')  # como o MACLE faz sem documento aberto
        if rota == 'DevolFornecedor' and acao == 'consOrigemWS':
            return tela_defeitos_fornecedor(s) if doc['forn'] else pagina('Origem', 'Selecione o fornecedor')
        if rota == 'DevolFornecedor' and acao == 'setTipoOperacaoDevolForn':
            doc['forn'] = q.get('codFornFilter')
            return tela_defeitos_fornecedor(s)
        if rota == 'DevolFornecedor' and acao == 'cad':
            return pagina('Origem', form_principal('origem'))
        if rota == 'DevolFornecedor' and acao == 'defeito':
            usados = {i['defeito'] for i in doc['itens']}
            marcados = [k[4:].replace('_', '.') for k, v in f.items() if k.startswith('chk_') and v == 'ativo']
            novo = next((c for c in marcados if c not in usados), None)
            if not novo:
                return pagina('Origem', 'Nenhum defeito marcado')
            doc['selecionado'] = novo
            d = estado['defeitos'][novo]
            notas = [c for c in compras_do_item(d['item']) if c['documento'].startswith(s['empresa'] + '.')
                     and d['tamanho'] in c['itens'][d['item']]['grade']]
            return pagina('Notas de origem', linhas_tabela([[c['documento'], c['documento'].split('.')[-1], c['emissao']] for c in notas]))
        if rota == 'DevolFornecedor' and acao == 'setOrigem':
            documento = f"{f.get('codEmpresa')}.{f.get('codFornecedor')}.{f.get('serie')}.{f.get('numero')}"
            doc['nf'] = documento
            c = compra(documento)
            livres = [cod for cod in defeitos_livres(s, doc['forn'])
                      if estado['defeitos'][cod]['item'] in c['itens'] and estado['defeitos'][cod]['tamanho'] in c['itens'][estado['defeitos'][cod]['item']]['grade']]
            return pagina('Defeitos da compra', linhas_tabela([[f'<input type="checkbox" name="chk_{cod.replace(".", "_")}">', cod] for cod in livres]))
        if rota == 'DevolFornecedor' and acao == 'defeitoByCompra':
            c = compra(doc['nf'])
            for k, v in f.items():
                if k.startswith('chk_') and v == 'ativo':
                    cod = k[4:].replace('_', '.')
                    d = estado['defeitos'][cod]
                    doc['itens'].append({'defeito': cod, 'item': d['item'], 'tamanho': d['tamanho'], 'qtd': d['quantidade'],
                                         'documento': doc['nf'], 'nitem': None if c['associarManual'] else 'auto'})
            return '<script>window.opener.gotoFechamto();</script>'
        if rota == 'DevolFornecedor' and acao == 'fechamto':
            pend = next((i for i in doc['itens'] if not i['nitem']), None)
            return tela_item_origem(s, pend) if pend else tela_fechamento(s)
        if rota == 'DevolFornecedor' and acao == 'setNItemOrigem':
            pend = next((i for i in doc['itens'] if not i['nitem'] and i['item'].endswith('.' + q.get('codItem', ''))
                         and i['tamanho'] == q.get('codTamanho')), None)
            if pend:
                pend['nitem'] = q.get('nitem')
            resto = [i for i in doc['itens'] if not i['nitem']]
            if resto:
                return tela_item_origem(s, resto[0])
            ligados = ''.join(f"<br>&#8618; {i['item']}.{i['tamanho']}" for i in doc['itens'])
            return pagina('Item de origem', f'Todos os itens foram associados{ligados}')
        if rota in ('DevolFornecedor', 'modEstoque/devolFornecedor/consFechamto.jsp') and acao in ('confirmaResolveNItem', 'consFechamto', None):
            return tela_fechamento(s)
        if rota == 'modCaixa/cadDadosTransporte.jsp':
            return tela_transporte()
        if rota == 'Transportadora' and acao == 'selecionaTransportadora':
            t = next((t for t in BASE['transportadoras'] if t['codigo'] == q.get('codTransp')), None)
            if not t:
                return 'ERRO: transportadora não encontrada'
            return ';'.join([t['nome'], t['cnpj'], t['ie'], t['endereco'], t['bairro'], t['cidade'], t['uf'], t['cep'], t['codigo']])
        if rota == 'DadosTransp' and metodo == 'POST' and acao == 'save':
            doc['transporte'] = f
            return 302, '', 'text/html', 'modEstoque/devolFornecedor/consFechamto.jsp'
        if rota == 'NFe' and acao == 'previewDANFE':
            n = next(estado['seq'])
            estado['pdfs'][n] = danfe(s)
            return f'tmp/{n}.pdf'
        if rota.startswith('tmp/') and rota.endswith('.pdf'):
            pdf = estado['pdfs'].get(int(re.sub(r'\D', '', rota) or 0))
            return (200, pdf, 'application/pdf') if pdf else (404, 'não encontrado')
        if rota == 'DevolFornecedor' and metodo == 'POST' and acao == 'continuarDepois':
            estado['pendentes'].append({'loja': s['empresa'], 'fornecedor': BASE['fornecedores'][doc['forn']]['nome'],
                                        'produtos': len(doc['itens']), 'qtd': sum(i['qtd'] for i in doc['itens'])})
            for i in doc['itens']:  # como o MACLE: o rascunho já põe o defeito em Enviado / Fornecedor
                estado['defeitos'][i['defeito']].update(situacao='Enviado', local='Fornecedor')
            s['doc'] = None
            return "<script>window.opener.location.href='DevolFornecedor?action=cons'</script>"
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
