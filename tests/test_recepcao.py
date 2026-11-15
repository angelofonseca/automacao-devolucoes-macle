"""Recepção pelo Telegram contra o simulador do MACLE (o Telegram é substituído: sem rede externa)."""
import unittest
from unittest import mock

from defeitos import casos, recepcao
from tests.apoio import MacleSimulado, config_teste, png_exemplo, resetar


def foto(codigo):
    return {'chat': {'id': 1}, 'photo': [{'file_id': 'pequena'}, {'file_id': 'grande'}], 'caption': codigo}


class TestLerMensagem(unittest.TestCase):
    def test_foto_com_codigo_e_descricao(self):
        msg = foto('1.1001.38 Salto soltando')
        self.assertEqual(recepcao.ler_mensagem(msg), ('1.1001.38', 'Salto soltando', 'grande', '.jpg'))

    def test_foto_enviada_como_arquivo(self):
        msg = {'document': {'file_id': 'x', 'mime_type': 'image/png', 'file_name': 'par.PNG'}, 'caption': '1.60000006'}
        self.assertEqual(recepcao.ler_mensagem(msg), ('1.60000006', '', 'x', '.png'))

    def test_texto_sem_foto(self):
        self.assertIsNone(recepcao.ler_mensagem({'text': '1.1001.38'}))


@mock.patch('defeitos.resiliencia.time.sleep', lambda s: None)
class TestAtender(unittest.TestCase):
    def setUp(self):
        self.respostas = []
        baixar = lambda cfg, file_id, destino: destino.write_bytes(png_exemplo()) and destino
        for alvo, falso in (('defeitos.recepcao.telegram.baixar_arquivo', baixar),
                            ('defeitos.recepcao.telegram.enviar', lambda cfg, texto: self.respostas.append(texto))):
            patcher = mock.patch(alvo, falso)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_registra_com_dados_do_erp(self):
        with MacleSimulado() as m:
            cfg = config_teste(m.url)
            resetar(cfg.entrada)
            registrado = recepcao.atender(cfg, foto('1.1001.38'))
            caso = {c['protocolo']: c for _, c, _ in casos.listar(cfg.entrada)}['6']
            self.assertEqual(caso['erp']['codigoDefeito'], '1.60000006')  # achou o defeito cadastrado no MACLE
            self.assertEqual(caso['loja'], '1')                           # a loja vem do defeito
            self.assertTrue((cfg.entrada / '6' / 'foto1.jpg').is_file())
            self.assertIn('✅ Caso #6 registrado', self.respostas[-1])
            self.assertEqual(registrado['protocolo'], '6')  # o main roda a rodada quando atender devolve o caso

    def test_sem_defeito_no_erp_nao_registra(self):
        with MacleSimulado() as m:
            cfg = config_teste(m.url)
            self.assertIsNone(recepcao.atender(cfg, foto('1.2001.41')))  # não há defeito do tamanho 41 no MACLE
            self.assertEqual(casos.listar(cfg.entrada), [])
            self.assertIn('nenhum defeito em aberto', self.respostas[-1])

    def test_mensagem_sem_codigo_recebe_ajuda(self):
        cfg = config_teste('http://127.0.0.1:9/Controle/')
        recepcao.atender(cfg, {'chat': {'id': 1}, 'text': 'oi'})
        self.assertEqual(self.respostas, [recepcao.AJUDA])


if __name__ == '__main__':
    unittest.main()
