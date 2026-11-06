"""Cliente HTTP do ERP: login com usuário/senha (cookie de sessão) e consulta de produto.

Toda chamada tem timeout. Falha temporária passa pelo retry; sessão expirada (401) faz
um novo login e repete a consulta uma vez.
"""
import re

import requests

from .resiliencia import ErroIntegracao, checar_status, com_retry

SERVICO = 'ERP'
CODIGO = re.compile(r'^\d+\.\d+\.\d+$')  # produto.referência.tamanho, ex.: 1.1001.37
CAMPOS_OBRIGATORIOS = ('marca', 'referencia', 'tamanho', 'custo', 'nf')


class ProdutoInvalido(Exception):
    """O ERP respondeu, mas o produto não existe ou veio sem os campos de que a triagem precisa."""


class ClienteERP:
    def __init__(self, cfg, ao_falhar=None):
        self.cfg = cfg
        self.ao_falhar = ao_falhar
        self.sessao = requests.Session()  # guarda o cookie entre as chamadas
        self.logado = False

    def _retry(self, funcao):
        return com_retry(funcao, SERVICO, tentativas=self.cfg.tentativas, ao_falhar=self.ao_falhar)

    def login(self):
        def tentar():
            r = self.sessao.post(f'{self.cfg.erp_url}/api/login', timeout=self.cfg.timeout,
                                 json={'usuario': self.cfg.erp_usuario, 'senha': self.cfg.erp_senha})
            checar_status(r, SERVICO)
            if 'sessao' not in self.sessao.cookies:
                raise ErroIntegracao(SERVICO, 'login sem cookie de sessão', temporaria=False)
        self._retry(tentar)
        self.logado = True

    def produto(self, codigo):
        """Dados do produto no ERP. Levanta ProdutoInvalido (não adianta repetir) ou ErroIntegracao."""
        if not CODIGO.match(codigo or ''):
            raise ProdutoInvalido(f'código {codigo!r} fora do formato produto.referência.tamanho')
        if not self.logado:
            self.login()

        def consultar():
            return self.sessao.get(f'{self.cfg.erp_url}/api/produtos/{codigo}', timeout=self.cfg.timeout)

        r = self._retry(lambda: _sem_5xx(consultar()))
        if r.status_code == 401:  # sessão expirou no ERP: entra de novo e repete uma vez
            self.login()
            r = self._retry(lambda: _sem_5xx(consultar()))
        if r.status_code == 404:
            raise ProdutoInvalido(f'produto {codigo} não encontrado no ERP')
        checar_status(r, SERVICO)
        return validar_produto(_json(r))


def _sem_5xx(resposta):
    """Deixa 401/404 voltarem para o chamador tratar; 5xx/429 viram erro temporário (retry)."""
    if resposta.status_code >= 500 or resposta.status_code == 429:
        checar_status(resposta, SERVICO)
    return resposta


def _json(resposta):
    try:
        return resposta.json()
    except ValueError:
        raise ErroIntegracao(SERVICO, 'resposta não é JSON', temporaria=False)


def validar_produto(dados):
    """Confere o formato do JSON antes de usar: campos presentes, custo numérico, NF com CNPJ e data."""
    if not isinstance(dados, dict):
        raise ProdutoInvalido('resposta do ERP não é um objeto JSON')
    faltando = [c for c in CAMPOS_OBRIGATORIOS if dados.get(c) in (None, '')]
    nf = dados.get('nf') or {}
    faltando += [f'nf.{c}' for c in ('numero', 'emissao', 'cnpjFornecedor') if not nf.get(c)]
    if faltando:
        raise ProdutoInvalido('ERP sem ' + ', '.join(faltando))
    try:
        custo = round(float(dados['custo']), 2)
    except (TypeError, ValueError):
        raise ProdutoInvalido(f"custo inválido: {dados['custo']!r}")
    return {
        'marca': dados['marca'],
        'referencia': dados['referencia'],
        'tamanho': str(dados['tamanho']),
        'cor': dados.get('cor'),
        'custo': custo,
        'nf': {'numero': str(nf['numero']), 'emissao': nf['emissao'], 'cnpjFornecedor': nf['cnpjFornecedor']},
    }
