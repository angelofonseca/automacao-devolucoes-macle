import unittest
from datetime import date
from unittest import mock

import requests

from defeitos.macle import consulta
from defeitos.macle.sessao import SessaoMacle, permitido
from defeitos.resiliencia import ErroIntegracao, com_retry, esconder
from mock_macle import servidor
from tests.apoio import MacleSimulado

HOJE = date(2026, 11, 9)


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

    def test_esconde_segredos(self):
        self.assertEqual(esconder('url /bot123:ABC/send', '123:ABC'), 'url /bot***/send')


class TestListaFechada(unittest.TestCase):
    def test_emitir_e_cancelar_nunca_passam(self):
        self.assertFalse(permitido('POST', 'DevolFornecedor', {'action': 'enviarSefaz'}))
        self.assertFalse(permitido('POST', 'DevolFornecedor', {'action': 'cancelDet'}))
        self.assertFalse(permitido('GET', 'NFe?action=geraCCe&codEmpresa=1&codNFe=2'))
        self.assertTrue(permitido('POST', 'DevolFornecedor', {'action': 'continuarDepois'}))
        self.assertTrue(permitido('GET', 'Defeito?action=edit&codEmpresa=1&codDefeito=60000001'))


@mock.patch('defeitos.resiliencia.time.sleep', lambda s: None)
class TestSessaoMacle(unittest.TestCase):
    def sessao(self, url, senha='senha-teste', timeout=2):
        return SessaoMacle(url, '6', '001', 'teste', senha, timeout=timeout)

    def test_consulta_completa(self):
        with MacleSimulado() as m:
            p = consulta.consultar(self.sessao(m.url), '1.1001.37', ['1'], HOJE)
        self.assertEqual((p['codigoDefeito'], p['marca'], p['custo']), ('1.60000001', 'AURORA', 129.9))
        self.assertEqual(p['nf']['cnpjFornecedor'], '00.100.200/0001-10')
        self.assertEqual(p['nf']['emissao'], '10/03/2026')

    def test_codigo_do_defeito_direto(self):
        with MacleSimulado() as m:
            p = consulta.consultar(self.sessao(m.url), '6.60000004', ['6'], HOJE)
        self.assertEqual((p['item'], p['tamanho'], p['quantidade']), ('1.2001', '42', 2))

    def test_503_duas_vezes_e_depois_responde(self):
        with MacleSimulado() as m:
            s = self.sessao(m.url)
            s.entrar()
            m.falhar(status=503, vezes=2)
            self.assertEqual(consulta.consultar(s, '1.1001.37', ['1'], HOJE)['marca'], 'AURORA')

    def test_macle_fora_do_ar(self):
        with MacleSimulado() as m:
            m.falhar(status=500, vezes=50)
            with self.assertRaises(ErroIntegracao) as ctx:
                consulta.consultar(self.sessao(m.url), '1.1001.37', ['1'], HOJE)
        self.assertTrue(ctx.exception.temporaria)

    def test_timeout_conta_como_temporario(self):
        with MacleSimulado() as m:
            s = self.sessao(m.url, timeout=1)
            s.entrar()
            m.falhar(atraso=1.5, vezes=1)
            self.assertEqual(consulta.consultar(s, '1.1001.37', ['1'], HOJE)['marca'], 'AURORA')

    def test_senha_errada_nao_repete(self):
        with MacleSimulado() as m:
            with self.assertRaises(ErroIntegracao) as ctx:
                self.sessao(m.url, senha='errada').entrar()
        self.assertFalse(ctx.exception.temporaria)

    def test_sessao_expirada_faz_novo_login(self):
        with MacleSimulado() as m:
            s = self.sessao(m.url)
            s.entrar()
            servidor.estado['sessoes'].clear()
            self.assertEqual(consulta.consultar(s, '1.1001.37', ['1'], HOJE)['marca'], 'AURORA')

    def test_produto_sem_defeito_e_codigo_invalido(self):
        with MacleSimulado() as m:
            s = self.sessao(m.url)
            with self.assertRaises(consulta.ProdutoNaoEncontrado):
                consulta.consultar(s, '1.1001.40', ['1'], HOJE)  # item existe, sem defeito desse tamanho
            with self.assertRaises(consulta.ProdutoNaoEncontrado):
                consulta.consultar(s, 'abc', ['1'], HOJE)


if __name__ == '__main__':
    unittest.main()
