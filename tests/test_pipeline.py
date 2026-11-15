"""Rodadas completas contra o simulador do MACLE, com Gmail e Telegram em modo arquivo (sem rede externa)."""
import email
import json
import unittest
from datetime import date, datetime
from email import policy
from unittest import mock

from defeitos import casos, novo_caso, pipeline, status
from tests.apoio import MacleSimulado, config_teste

HOJE, AGORA = date(2026, 11, 9), datetime(2026, 11, 9, 14, 0)


def situacao(cfg):
    return {c['protocolo']: c['status'] for _, c, _ in casos.listar(cfg.entrada) if c}


@mock.patch('defeitos.resiliencia.time.sleep', lambda s: None)
class TestPipeline(unittest.TestCase):
    def test_fluxo_completo_ate_a_autorizacao(self):
        with MacleSimulado() as m:
            cfg = config_teste(m.url)
            novo_caso.resetar(cfg.entrada)
            r = pipeline.executar(cfg, HOJE, AGORA)
            self.assertTrue(r['ok'], r['falhas'])
            # 1 e 2: Aurora loja 1 = R$ 219,80 < R$ 300 -> aguardando; 3 e 4: Passo Firme loja 6 = 3 pares -> e-mail;
            # 5: Trilha sem e-mail -> decisão
            self.assertEqual(situacao(cfg), {'1': 'aguardando_lote', '2': 'aguardando_lote', '3': 'aguardando_aprovacao',
                                             '4': 'aguardando_aprovacao', '5': 'aguardando_decisao'})
            eml = email.message_from_bytes((cfg.saida / 'rascunhos' / 'L6-passo-firme.eml').read_bytes(), policy=policy.default)
            self.assertEqual(eml['To'], 'qualidade@passofirme.example')
            self.assertIn('NF ORIGEM: 7720', eml.get_body().get_content())

            # par novo fecha o lote da Aurora (assunto "fora do prazo" por causa do caso 2)
            novo_caso.criar(cfg.entrada, '1', '1.1001.38', 1, 'Salto soltando')
            r = pipeline.executar(cfg, HOJE, AGORA)
            self.assertEqual(r['rascunhos'][0]['protocolos'], ['1', '2', '6'])
            self.assertTrue(r['rascunhos'][0]['assunto'].endswith('fora do prazo'))

            # a pessoa envia o e-mail e a fábrica autoriza: daqui a NFD é com a pessoa, a automação não mexe mais
            entrada = ['--entrada', str(cfg.entrada)]
            status.main(['--protocolos', '1,2,6', '--para', 'enviado'] + entrada)
            status.main(['--protocolos', '1,2,6', '--para', 'autorizado'] + entrada)
            self.assertNotEqual(status.main(['--protocolos', '1', '--para', 'novo'] + entrada), 0)  # autorizado é final

            # sem novidade: não cria nada e não avisa
            r = pipeline.executar(cfg, HOJE, AGORA)
            self.assertTrue(r['ok'], r['falhas'])
            self.assertEqual({situacao(cfg)[p] for p in ('1', '2', '6')}, {'autorizado'})
            self.assertEqual((r['rascunhos'], r['avisou']), ([], False))

    def test_macle_fora_do_ar_avisa_e_mantem_os_casos(self):
        with MacleSimulado() as m:
            cfg = config_teste(m.url)
            novo_caso.resetar(cfg.entrada)
            m.falhar(status=503, vezes=100)
            r = pipeline.executar(cfg, HOJE, AGORA)
        self.assertFalse(r['ok'])
        self.assertIn('MACLE: HTTP 503', r['falhas'][0])
        self.assertTrue(r['avisou'])
        self.assertEqual(set(situacao(cfg).values()), {'novo'})
        tentativas = [json.loads(l) for l in cfg.log.read_text(encoding='utf-8').splitlines()]
        self.assertEqual([t['tentativa'] for t in tentativas], [1, 2, 3])

    def test_caso_invalido_e_produto_sem_defeito(self):
        with MacleSimulado() as m:
            cfg = config_teste(m.url)
            novo_caso.criar(cfg.entrada, '1', '1.1001.40', 1, 'x')
            (cfg.entrada / '9').mkdir()
            (cfg.entrada / '9' / 'caso.json').write_text('{quebrado', encoding='utf-8')
            r = pipeline.executar(cfg, HOJE, AGORA)
        self.assertEqual(r['bloqueados'][0]['protocolo'], '1')
        self.assertEqual(r['invalidos'][0]['pasta'], '9')
        self.assertEqual(situacao(cfg)['1'], 'aguardando_decisao')


if __name__ == '__main__':
    unittest.main()
