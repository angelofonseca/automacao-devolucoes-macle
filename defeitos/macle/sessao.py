"""Sessão HTTP com o MACLE: login com empresa/terminal/usuário/senha, cookie, lista fechada de ações e retry.

Segurança: o usuário do ERP pode alterar dados, então a sessão só aceita as URLs e as ações listadas aqui.
Emitir a nota (SEFAZ) e cancelar não estão na lista: nunca são enviados, nem por engano.
"""
import re
from dataclasses import dataclass
from urllib.parse import urljoin

import requests

from ..resiliencia import ErroIntegracao, REDE, com_retry
from .html import decodificar

SERVICO = 'MACLE'

GET_CONSULTA = [
    r'Defeito\?action=cons', r'Defeito\?action=cadFiltro', r'Defeito\?action=edit&codEmpresa=\d+&codDefeito=\d+',
    r'Item\?action=edit&codEmpItem=\d+&codItem=\d+', r'modEstoque/item/consHistoricoCompra\.jsp\?codEmpItem=\d+&codItem=\d+',
    r'modCompras/docCompra/consDataDocCompra\.jsp\?codEmpresa=\d+&origem=\d+&serie=\w+&numero=\d+',
]
GET_NFD = [
    r'DevolFornecedor\?action=cons', r'DevolFornecedor\?action=add&resY=\d+', r'DevolFornecedor\?action=(consOrigemWS|consFechamto)',
    r'DevolFornecedor\?action=setTipoOperacaoDevolForn&codFornFilter=\d+&tipoOperacao=MERCADORIA',
    r'DevolFornecedor\?action=fechamto&offset=\d+',
    r'DevolFornecedor\?action=setNItemOrigem&nitem=\d+&codItem=\d+&codTamanho=\w+&chave=\d{44}&codEmpOrig=\d+&origemOrig=\d+&serieOrig=\w+&numeroOrig=\d+',
    r'DevolFornecedor\?action=confirmaResolveNItem&chave=\d{44}&codEmpOrig=\d+&origemOrig=\d+&serieOrig=\w+&numeroOrig=\d+',
    r'DevolFornecedor\?action=cad&doAction=origem&cleanOBS=true&infAdicional=',
    r'modEstoque/devolFornecedor/(continuaDevolucao|consFechamto)\.jsp',
    r'modCaixa/cadDadosTransporte\.jsp\?codEmpresa=null&origem=0&serie=null&numero=null&resY=\d+&tipoDoc=4',
    r'Transportadora\?action=selecionaTransportadora&codTransp=\d+',
    r'NFe\?action=previewDANFE&tipo=devolForn&obs=[^&]*&rndval=\d+', r'tmp/\d+\.pdf',
]
# POST: endereço -> valores permitidos de "action". Emitir e cancelar não estão aqui.
POST_PERMITIDO = {'Defeito': {'setFilterDefeito'},
                  'DevolFornecedor': {'add', 'defeito', 'setOrigem', 'defeitoByCompra', 'continuarDepois'},
                  'DadosTransp': {'save'}}
PROIBIDO = re.compile(r'cancel|sefaz|transmit|emit|envia|autoriza|inutiliz|exclu|delete', re.I)


def permitido(metodo, caminho, campos=None):
    if metodo == 'GET':
        return any(re.fullmatch(r, caminho) for r in GET_CONSULTA + GET_NFD)
    acao = (campos or {}).get('action', '')
    return acao in POST_PERMITIDO.get(caminho, set()) and not PROIBIDO.search(acao)


@dataclass
class Resposta:
    status: int
    html: str
    conteudo: bytes
    location: str
    login: bool  # a resposta é a tela de login (sessão caiu)


class SessaoMacle:
    def __init__(self, url, empresa, terminal, usuario, senha, timeout=15, tentativas=3, ao_falhar=None):
        self.url = url.rstrip('/') + '/'
        self.empresa, self.terminal = str(empresa), str(terminal)
        self._usuario, self._senha = usuario, senha
        self.timeout, self.tentativas, self.ao_falhar = timeout, tentativas, ao_falhar
        self.http = requests.Session()
        self.logado = False

    def _req(self, caminho, campos=None):
        def tentar():
            r = self.http.request('POST' if campos is not None else 'GET', urljoin(self.url, caminho), data=campos,
                                  allow_redirects=False, timeout=self.timeout)
            if r.status_code >= 500 or r.status_code == 429:
                raise ErroIntegracao(SERVICO, f'HTTP {r.status_code} em {caminho.split("?")[0] or "página de login"}', temporaria=True, status=r.status_code)
            return r
        try:
            r = com_retry(tentar, SERVICO, tentativas=self.tentativas, ao_falhar=self.ao_falhar)
        except REDE:  # não deveria chegar aqui (com_retry converte), mas nunca deixa vazar a URL com dados
            raise ErroIntegracao(SERVICO, 'ERP fora do ar ou sem resposta', temporaria=True)
        html = decodificar(r.content)
        loc = r.headers.get('location', '')
        login = (300 <= r.status_code < 400 and bool(re.search(r'login|/Controle/?(\?|$)', loc, re.I))) \
            or bool(re.search(r'MACLE Controle - Login|name=["\']?senha\b', html, re.I))
        return Resposta(r.status_code, html, r.content, loc, login)

    def entrar(self):
        """Login como a tela faz: abre a página inicial (cookie) e posta empresa, terminal, usuário e senha."""
        self.http.cookies.clear()
        self._req('')
        r = self._req('Login', {'action': 'loggin', 'url': 'null', 'empresa': self.empresa, 'terminal': self.terminal,
                                'usuario': self._usuario, 'senha': self._senha, 'biometria': 'false'})
        if r.status >= 400 or (r.status < 300 and r.login):
            raise ErroIntegracao(SERVICO, f'login recusado na empresa {self.empresa}: confira MACLE_USUARIO/MACLE_SENHA', temporaria=False)
        self.logado = True

    def _chamar(self, metodo, caminho, campos=None, relogar=True):
        if not permitido(metodo, caminho, campos):
            raise ErroIntegracao(SERVICO, f'ação fora da lista permitida: {metodo} {caminho.split("&")[0]}', temporaria=False)
        if not self.logado:
            self.entrar()
        r = self._req(caminho, campos)
        if r.login and relogar:  # sessão expirou: entra de novo e repete uma vez
            self.entrar()
            r = self._req(caminho, campos)
        if r.login:
            raise ErroIntegracao(SERVICO, 'sessão do ERP recusada após novo login', temporaria=False)
        for _ in range(3):  # segue redirecionamentos, mas só para dentro da lista
            if not (300 <= r.status < 400 and r.location):
                break
            prox = re.sub(r';jsessionid=[^?]*', '', urljoin(self.url, r.location).replace(self.url, ''), flags=re.I)
            if not permitido('GET', prox):
                raise ErroIntegracao(SERVICO, f'ERP redirecionou para fora da lista: {prox.split("?")[0]}', temporaria=False)
            r = self._req(prox)
        if r.status >= 400:
            raise ErroIntegracao(SERVICO, f'ERP respondeu {r.status} em {caminho.split("?")[0]}', temporaria=False, status=r.status)
        return r

    # Consulta
    def get(self, caminho):
        return self._chamar('GET', caminho).html

    def filtrar_defeitos(self, campos):
        r = self._chamar('POST', 'Defeito', campos)
        if 'Defeito?action=cons' not in r.html:
            raise ErroIntegracao(SERVICO, 'filtro de defeitos recusado pelo ERP', temporaria=False)

    # NFD: no meio do documento, sessão caída não pode relogar (o documento aberto se perde)
    def abrir(self, caminho):
        return self._chamar('GET', caminho, relogar=not caminho.startswith(('DevolFornecedor', 'modEstoque/devol', 'modCaixa', 'NFe', 'tmp/', 'Transportadora'))).html

    def enviar(self, caminho, campos):
        return self._chamar('POST', caminho, campos, relogar=caminho == 'Defeito').html

    def baixar(self, caminho):
        return self._chamar('GET', caminho, relogar=False).conteudo
