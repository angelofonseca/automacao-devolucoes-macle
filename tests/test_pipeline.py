"""Rodada completa contra o ERP simulado, com Gmail e Telegram em modo arquivo (sem rede externa)."""
import email
from email import policy
import json
import unittest
from datetime import date, datetime
from unittest import mock

from defeitos import casos, novo_caso, pipeline
from tests.apoio import ERPSimulado, config_teste

HOJE, AGORA = date(2026, 11, 6), datetime(2026, 11, 6, 14, 0)


def status(cfg):
    return {c['protocolo']: c['status'] for _, c, _ in casos.listar(cfg.entrada) if c}


@mock.patch('defeitos.resiliencia.time.sleep', lambda s: None)
class TestPipeline(unittest.TestCase):
    def test_rodada_com_os_casos_de_exemplo(self):
        with ERPSimulado() as erp:
            cfg = config_teste(erp.url)
            novo_caso.resetar(cfg.entrada)
            r = pipeline.executar(cfg, HOJE, AGORA)

            self.assertTrue(r['ok'], r['falhas'])
            # casos 1 e 2: lote Aurora loja 1 = R$ 219,80 < R$ 300 -> aguardando
            # casos 3 e 4: Passo Firme loja 6 = 3 pares -> rascunho
            # caso 5: Trilha sem e-mail -> decisão
            self.assertEqual(status(cfg), {'1': 'aguardando_lote', '2': 'aguardando_lote', '3': 'aguardando_aprovacao',
                                           '4': 'aguardando_aprovacao', '5': 'aguardando_decisao'})
            eml = email.message_from_bytes((cfg.saida / 'rascunhos' / 'L6-passo-firme.eml').read_bytes(), policy=policy.default)
            self.assertEqual(eml['To'], 'qualidade@passofirme.example')
            self.assertEqual(len(list(eml.iter_attachments())), 2)
            self.assertIn('Rascunho pronto', (cfg.saida / 'telegram.log').read_text(encoding='utf-8'))

            # um par a mais fecha o lote da Aurora: o rascunho sai com "fora do prazo" por causa do caso 2
            novo_caso.criar(cfg.entrada, '1', '1.1001.38', 1, 'Salto soltando')
            r = pipeline.executar(cfg, HOJE, AGORA)
            self.assertEqual(r['rascunhos'][0]['protocolos'], ['1', '2', '6'])
            self.assertTrue(r['rascunhos'][0]['assunto'].endswith('fora do prazo'))

            # rodar de novo sem novidade: não cria rascunho e não avisa
            r = pipeline.executar(cfg, HOJE, AGORA)
            self.assertEqual((r['rascunhos'], r['avisou']), ([], False))

    def test_erp_fora_do_ar_avisa_e_mantem_os_casos(self):
        with ERPSimulado() as erp:
            cfg = config_teste(erp.url)
            novo_caso.resetar(cfg.entrada)
            erp.falhar(status=503, vezes=100)
            r = pipeline.executar(cfg, HOJE, AGORA)
        self.assertFalse(r['ok'])
        self.assertIn('ERP: HTTP 503', r['falhas'][0])
        self.assertTrue(r['avisou'])
        self.assertEqual(set(status(cfg).values()), {'novo'})
        tentativas = [json.loads(l) for l in cfg.log.read_text(encoding='utf-8').splitlines()]
        self.assertEqual([t['tentativa'] for t in tentativas], [1, 2, 3])

    def test_caso_invalido_e_produto_inexistente(self):
        with ERPSimulado() as erp:
            cfg = config_teste(erp.url)
            novo_caso.criar(cfg.entrada, '1', '1.0000.00', 1, 'x')
            (cfg.entrada / '9').mkdir()
            (cfg.entrada / '9' / 'caso.json').write_text('{quebrado', encoding='utf-8')
            r = pipeline.executar(cfg, HOJE, AGORA)
        self.assertEqual(r['bloqueados'][0]['protocolo'], '1')
        self.assertEqual(r['invalidos'][0]['pasta'], '9')
        self.assertEqual(status(cfg)['1'], 'aguardando_decisao')


if __name__ == '__main__':
    unittest.main()
