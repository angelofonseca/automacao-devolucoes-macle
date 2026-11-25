"""Laço principal: foto no chat vira caso e rodada; rodada agendada; chats de fora são ignorados."""
import unittest
from unittest import mock

from defeitos import casos, main
from tests.apoio import MacleSimulado, config_teste, integracoes_falsas, png_exemplo


def update(n, chat, legenda):
    return {'update_id': n, 'message': {'chat': {'id': chat}, 'photo': [{'file_id': f'f{n}'}], 'caption': legenda}}


@mock.patch('defeitos.resiliencia.time.sleep', lambda s: None)
class TestOuvir(unittest.TestCase):
    def ouvir(self, cfg, lotes, relogio=None):
        """Roda main.ouvir entregando cada lote de updates numa volta do long polling; depois Ctrl+C."""
        entregas = iter(lotes)

        def receber(cfg, offset, espera):
            if relogio:
                relogio[0] += espera  # cada volta do long polling leva `espera` segundos
            try:
                return next(entregas)
            except StopIteration:
                raise KeyboardInterrupt
        baixar = lambda cfg, file_id, destino: destino.write_bytes(png_exemplo()) and destino
        rodar = mock.Mock(wraps=main.rodar)
        with mock.patch('defeitos.main.telegram.receber', receber), \
                mock.patch('defeitos.recepcao.telegram.baixar_arquivo', baixar), mock.patch('defeitos.main.rodar', rodar):
            self.assertEqual(main.ouvir(cfg), 0)
        return rodar

    def test_fotos_viram_casos_e_rodadas(self):
        with MacleSimulado() as m, integracoes_falsas() as fora:
            cfg = config_teste(m.url)
            rodar = self.ouvir(cfg, [[update(10, 1, '1.2001.40'), update(11, 999, '1.2001.42')],
                                     [update(12, 1, '1.2001.42 Palmilha soltando')],
                                     [update(13, 1, '1.2001.42 Costura do bico')]])
            todos = {c['protocolo']: c for _, c, _ in casos.listar(cfg.entrada)}
        # o chat 999 foi ignorado; as duas fotos do 1.2001.42 são os dois pares do defeito 1.60000004
        self.assertEqual([(c['codigo'], c['erp']['codigoDefeito'], c['defeito']) for c in todos.values()],
                         [('1.2001.40', '1.60000003', 'Costura aberta'), ('1.2001.42', '1.60000004', 'Palmilha soltando'),
                          ('1.2001.42', '1.60000004', 'Costura do bico')])
        self.assertEqual(rodar.call_count, 4)  # ao subir + uma por caso registrado
        self.assertEqual({c['status'] for c in todos.values()}, {'aguardando_aprovacao'})  # 3 pares: rascunho
        self.assertEqual(len(fora['rascunhos']), 1)
        self.assertEqual(len(list(fora['rascunhos'][0].iter_attachments())), 3)  # uma foto por par
        self.assertIn('(par 2 de 2)', fora['telegram'][-2])
        self.assertEqual((cfg.saida / 'telegram_offset.txt').read_text(), '14')

    def test_rodada_agendada_quando_passa_o_intervalo(self):
        relogio = [0]
        with MacleSimulado() as m, integracoes_falsas(), mock.patch('defeitos.main.time.monotonic', lambda: relogio[0]):
            cfg = config_teste(m.url, intervalo_min=1)
            rodar = self.ouvir(cfg, [[], [], []], relogio)  # 3 voltas de 30 s
        self.assertEqual(rodar.call_count, 2)  # ao subir (t=0) e quando passou 1 min (t=60)


class TestConfig(unittest.TestCase):
    def test_gmail_e_telegram_sao_obrigatorios(self):
        from defeitos import config
        base = {'GMAIL_USER': 'g@x', 'GMAIL_APP_PASSWORD': 'p', 'TELEGRAM_BOT_TOKEN': 't', 'TELEGRAM_CHAT_ID': '1'}
        for falta in ('GMAIL_USER', 'GMAIL_APP_PASSWORD', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID'):
            env = {**base, falta: ''}
            with self.subTest(falta=falta), mock.patch.dict('os.environ', env), mock.patch('defeitos.config.load_dotenv'):
                with self.assertRaisesRegex(config.ConfigInvalida, falta):
                    config.carregar()
        with mock.patch.dict('os.environ', base), mock.patch('defeitos.config.load_dotenv'):
            cfg = config.carregar()  # sem MACLE_USUARIO/MACLE_SENHA: o simulador aceita qualquer login
        self.assertEqual((cfg.gmail_user, cfg.loja['numero'], cfg.macle_usuario), ('g@x', '1', ''))


if __name__ == '__main__':
    unittest.main()
