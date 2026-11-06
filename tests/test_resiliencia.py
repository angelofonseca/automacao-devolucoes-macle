import unittest
from unittest import mock

import requests

from defeitos.erp import ClienteERP, ProdutoInvalido
from defeitos.resiliencia import ErroIntegracao, com_retry, esconder
from mock_erp import servidor
from tests.apoio import ERPSimulado, config_teste


class TestRetry(unittest.TestCase):
    def test_repete_falha_temporaria_ate_dar_certo(self):
        respostas = [requests.ConnectionError(), ErroIntegracao('X', 'HTTP 503', True), 'ok']

        def chamada():
            r = respostas.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        esperas = []
        self.assertEqual(com_retry(chamada, 'X', tentativas=3, dormir=esperas.append), 'ok')
        self.assertEqual(esperas, [1.0, 2.0])  # backoff exponencial

    def test_nao_repete_falha_permanente(self):
        chamadas = []

        def chamada():
            chamadas.append(1)
            raise ErroIntegracao('X', 'HTTP 401', temporaria=False)
        with self.assertRaises(ErroIntegracao):
            com_retry(chamada, 'X', tentativas=3, dormir=lambda s: None)
        self.assertEqual(len(chamadas), 1)

    def test_desiste_depois_das_tentativas(self):
        with self.assertRaises(ErroIntegracao) as ctx:
            com_retry(lambda: (_ for _ in ()).throw(requests.Timeout()), 'X', tentativas=3, dormir=lambda s: None)
        self.assertTrue(ctx.exception.temporaria)

    def test_esconde_segredos(self):
        self.assertEqual(esconder('url /bot123:ABC/send', '123:ABC'), 'url /bot***/send')


@mock.patch('defeitos.resiliencia.time.sleep', lambda s: None)
class TestClienteERP(unittest.TestCase):
    def test_consulta_com_login(self):
        with ERPSimulado() as erp:
            p = ClienteERP(config_teste(erp.url)).produto('1.1001.37')
        self.assertEqual(p['marca'], 'Aurora')
        self.assertEqual(p['nf']['cnpjFornecedor'], '00.100.200/0001-10')

    def test_503_duas_vezes_e_depois_responde(self):
        with ERPSimulado() as erp:
            cliente = ClienteERP(config_teste(erp.url))
            cliente.login()
            erp.falhar(status=503, vezes=2)
            self.assertEqual(cliente.produto('1.1001.37')['custo'], 129.9)

    def test_erp_fora_do_ar(self):
        with ERPSimulado() as erp:
            erp.falhar(status=500, vezes=10)
            with self.assertRaises(ErroIntegracao) as ctx:
                ClienteERP(config_teste(erp.url)).produto('1.1001.37')
        self.assertTrue(ctx.exception.temporaria)

    def test_timeout_conta_como_temporario(self):
        with ERPSimulado() as erp:
            cliente = ClienteERP(config_teste(erp.url, timeout=1))
            cliente.login()
            erp.falhar(atraso=1.5, vezes=1)
            self.assertEqual(cliente.produto('1.1001.37')['marca'], 'Aurora')  # 2ª tentativa responde

    def test_senha_errada_nao_repete(self):
        with ERPSimulado() as erp:
            with self.assertRaises(ErroIntegracao) as ctx:
                ClienteERP(config_teste(erp.url, erp_senha='errada')).produto('1.1001.37')
        self.assertEqual(ctx.exception.status, 401)
        self.assertFalse(ctx.exception.temporaria)

    def test_sessao_expirada_faz_novo_login(self):
        with ERPSimulado() as erp:
            cliente = ClienteERP(config_teste(erp.url))
            cliente.login()
            servidor.sessoes.clear()
            self.assertEqual(cliente.produto('1.1001.37')['marca'], 'Aurora')

    def test_produto_inexistente_e_codigo_invalido(self):
        with ERPSimulado() as erp:
            cliente = ClienteERP(config_teste(erp.url))
            with self.assertRaises(ProdutoInvalido):
                cliente.produto('1.0000.00')
            with self.assertRaises(ProdutoInvalido):
                cliente.produto('abc')


if __name__ == '__main__':
    unittest.main()
