import unittest
from datetime import date, datetime

from defeitos import triagem

CADASTRO = {
    'lojas': {'1': {'razao': 'Loja Teste', 'cnpj': '00.000.000/0001-00'}},
    'fornecedores': [
        {'id': 'a', 'nome': 'Fábrica A', 'cnpjRaiz': ['11111111'], 'contato': {'email': 'a@a.example'},
         'prazo': {'meses': 12}, 'minimoLote': 200, 'minimoPares': 0},
        {'id': 'b', 'nome': 'Fábrica B', 'cnpjRaiz': ['22222222'], 'contato': {'email': 'b@b.example'},
         'prazo': None, 'minimoLote': 0, 'minimoPares': 3},
        {'id': 'c', 'nome': 'Fábrica C', 'cnpjRaiz': ['33333333'], 'contato': {'email': None}, 'prazo': None},
    ],
}
HOJE, AGORA = date(2026, 11, 6), datetime(2026, 11, 6, 9, 0)


def caso(protocolo, cnpj, custo, emissao='01/06/2026', loja='1', quantidade=1):
    return {'protocolo': protocolo, 'loja': loja, 'codigo': '1.1.1', 'quantidade': quantidade, 'defeito': 'sola',
            'status': 'novo', 'fotos': [],
            'erp': {'referencia': 'REF', 'tamanho': '38', 'custo': custo,
                    'nf': {'numero': '10', 'emissao': emissao, 'cnpjFornecedor': cnpj}}}


class TestTriagem(unittest.TestCase):
    def test_lote_abaixo_do_minimo_aguarda(self):
        p = triagem.planejar([caso('1', '11.111.111/0001-11', 120.0)], CADASTRO, HOJE, AGORA)
        self.assertEqual(p['montarRascunho'], [])
        self.assertEqual(p['aguardarLote'][0]['falta'], 80.0)

    def test_lote_que_atinge_o_minimo_vira_rascunho(self):
        p = triagem.planejar([caso('1', '11.111.111/0001-11', 120.0), caso('2', '11.111.111/0002-22', 90.0)],
                             CADASTRO, HOJE, AGORA)
        r = p['montarRascunho'][0]
        self.assertEqual(r['protocolos'], ['1', '2'])
        self.assertEqual(r['total'], 210.0)
        self.assertEqual(r['para'], ['a@a.example'])
        self.assertIn('Seguem 2 pares', r['corpo'])
        self.assertTrue(r['corpo'].startswith('Bom dia'))

    def test_lojas_diferentes_sao_lotes_diferentes(self):
        p = triagem.planejar([caso('1', '11.111.111/0001-11', 150.0, loja='1'), caso('2', '11.111.111/0001-11', 150.0, loja='6')],
                             CADASTRO, HOJE, AGORA)
        self.assertEqual(len(p['aguardarLote']), 2)

    def test_minimo_de_pares(self):
        p = triagem.planejar([caso('1', '22.222.222/0001-22', 50.0, quantidade=2)], CADASTRO, HOJE, AGORA)
        self.assertEqual(p['aguardarLote'][0]['pares'], 2)
        p = triagem.planejar([caso('1', '22.222.222/0001-22', 50.0, quantidade=3)], CADASTRO, HOJE, AGORA)
        self.assertEqual(p['montarRascunho'][0]['pares'], 3)

    def test_fora_do_prazo_nao_trava_mas_marca_o_assunto(self):
        p = triagem.planejar([caso('1', '11.111.111/0001-11', 300.0, emissao='01/01/2025')], CADASTRO, HOJE, AGORA)
        r = p['montarRascunho'][0]
        self.assertTrue(r['assunto'].endswith('fora do prazo'))
        self.assertIn('fora do prazo', r['avisos'][0])

    def test_trava_sem_contato_e_fora_do_cadastro(self):
        p = triagem.planejar([caso('1', '33.333.333/0001-33', 10.0), caso('2', '99.999.999/0001-99', 10.0)],
                             CADASTRO, HOJE, AGORA)
        motivos = {b['protocolo']: b['motivo'] for b in p['bloquear']}
        self.assertIn('sem e-mail', motivos['1'])
        self.assertIn('fora do cadastro', motivos['2'])
        self.assertEqual(p['montarRascunho'], [])


if __name__ == '__main__':
    unittest.main()
