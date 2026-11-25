"""Rodadas completas contra o simulador do MACLE, com Gmail e Telegram falsos (sem rede externa)."""
import json
import unittest
from datetime import date, datetime
from unittest import mock

from defeitos import casos, pipeline, status
from tests.apoio import MacleSimulado, config_teste, criar_caso, integracoes_falsas, resetar

HOJE, AGORA = date(2026, 11, 9), datetime(2026, 11, 9, 14, 0)


def situacao(cfg):
    return {c['protocolo']: c['status'] for _, c, _ in casos.listar(cfg.entrada) if c}


@mock.patch('defeitos.resiliencia.time.sleep', lambda s: None)
class TestPipeline(unittest.TestCase):
    def test_fluxo_completo_ate_o_rascunho(self):
        with MacleSimulado() as m, integracoes_falsas() as fora:
            cfg = config_teste(m.url)
            resetar(cfg.entrada)
            r = pipeline.executar(cfg, HOJE, AGORA)
            self.assertTrue(r['ok'], r['falhas'])
            # 1: Aurora R$ 129,90 < R$ 300 -> aguardando; 2: sandália Aurora, NF de 15/08/2025 (12 meses) -> trava;
            # 3 e 4: Passo Firme = 3 pares -> e-mail; 5: Trilha sem e-mail -> trava
            self.assertEqual(situacao(cfg), {'1': 'aguardando_lote', '2': 'aguardando_decisao', '3': 'aguardando_aprovacao',
                                             '4': 'aguardando_aprovacao', '5': 'aguardando_decisao'})
            bloqueio = {b['protocolo']: b['motivo'] for b in r['bloqueados']}
            self.assertIn('fora do prazo', bloqueio['2'])
            eml = fora['rascunhos'][0]
            self.assertEqual((eml['From'], eml['To']), ('loja@teste.example', 'qualidade@passofirme.example'))
            self.assertIn('NF ORIGEM: 7720', eml.get_body().get_content())
            self.assertIn('Loja Centro', eml.get_body().get_content())
            self.assertEqual(len(list(eml.iter_attachments())), 2)  # uma foto por caso
            self.assertIn('✉️ Rascunho pronto para revisar: Passo Firme', fora['telegram'][0])

            # dois pares novos fecham o lote da Aurora: 3 x R$ 129,90 = R$ 389,70
            criar_caso(cfg.entrada, '1', '1.1001.38', 1, 'Salto soltando')
            criar_caso(cfg.entrada, '1', '1.1001.36', 1, 'Salto quebrado')
            r = pipeline.executar(cfg, HOJE, AGORA)
            self.assertEqual(r['rascunhos'][0]['protocolos'], ['1', '6', '7'])
            self.assertEqual((r['rascunhos'][0]['total'], r['rascunhos'][0]['assunto']), (389.7, 'Produtos com defeito'))

            # rascunho criado é o fim: a rodada seguinte não mexe nos casos e não avisa
            r = pipeline.executar(cfg, HOJE, AGORA)
            self.assertEqual({situacao(cfg)[p] for p in ('1', '3', '4', '6', '7')}, {'aguardando_aprovacao'})
            self.assertEqual((r['rascunhos'], r['avisou']), ([], False))
            self.assertEqual(len(fora['rascunhos']), 2)

    def test_caso_travado_reabre_e_volta_para_a_triagem(self):
        with MacleSimulado() as m, integracoes_falsas():
            cfg = config_teste(m.url)
            resetar(cfg.entrada)
            pipeline.executar(cfg, HOJE, AGORA)
            entrada = ['--entrada', str(cfg.entrada)]
            self.assertEqual(status.main(['--reabrir', '5'] + entrada), 0)
            self.assertEqual(situacao(cfg)['5'], 'novo')
            self.assertNotEqual(status.main(['--reabrir', '3'] + entrada), 0)  # rascunho criado não reabre

    def test_macle_fora_do_ar_avisa_e_mantem_os_casos(self):
        with MacleSimulado() as m, integracoes_falsas() as fora:
            cfg = config_teste(m.url)
            resetar(cfg.entrada)
            m.falhar(status=503, vezes=100)
            r = pipeline.executar(cfg, HOJE, AGORA)
        self.assertFalse(r['ok'])
        self.assertIn('MACLE: HTTP 503', r['falhas'][0])
        self.assertTrue(r['avisou'])
        self.assertIn('🚨 Falha: MACLE: HTTP 503', fora['telegram'][0])
        self.assertEqual(set(situacao(cfg).values()), {'novo'})
        tentativas = [json.loads(l) for l in cfg.log.read_text(encoding='utf-8').splitlines()]
        self.assertEqual([t['tentativa'] for t in tentativas], [1, 2, 3])

    def test_caso_invalido_e_produto_sem_defeito(self):
        with MacleSimulado() as m, integracoes_falsas():
            cfg = config_teste(m.url)
            criar_caso(cfg.entrada, '1', '1.1001.40', 1, 'x')
            (cfg.entrada / '9').mkdir()
            (cfg.entrada / '9' / 'caso.json').write_text('{quebrado', encoding='utf-8')
            r = pipeline.executar(cfg, HOJE, AGORA)
        self.assertEqual(r['bloqueados'][0]['protocolo'], '1')
        self.assertEqual(r['invalidos'][0]['pasta'], '9')
        self.assertEqual(situacao(cfg)['1'], 'aguardando_decisao')


if __name__ == '__main__':
    unittest.main()
