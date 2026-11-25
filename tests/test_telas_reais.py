"""Fidelidade ao MACLE real: roda os parsers em páginas salvas do sistema de verdade.

As páginas reais têm dados de clientes e fornecedores e NUNCA vão para o git. Para rodar, aponte
MACLE_FIXTURES para a pasta onde elas estão salvas (defeito.html, item.html, historico.html, ...):

  MACLE_FIXTURES=C:\\caminho\\fixtures python -m unittest tests.test_telas_reais
"""
import os
import unittest
from datetime import date
from pathlib import Path

from defeitos.macle import telas
from defeitos.macle.html import decodificar

PASTA = Path(os.environ.get('MACLE_FIXTURES', ''))


@unittest.skipUnless(os.environ.get('MACLE_FIXTURES') and PASTA.is_dir(), 'MACLE_FIXTURES não definido (páginas reais ficam fora do git)')
class TestTelasReais(unittest.TestCase):
    def ler(self, nome):
        arq = PASTA / nome
        if not arq.exists():
            self.skipTest(f'{nome} não está em MACLE_FIXTURES')
        return decodificar(arq.read_bytes())

    def test_consulta(self):
        d = telas.ler_defeito(self.ler('defeito.html'))
        self.assertRegex(d['item'], r'^\d+\.\d+$')
        self.assertTrue(d['tamanho'] and d['situacao'] and d['cadastro'])
        self.assertTrue(telas.ler_item(self.ler('item.html'))['referencia'])
        h = telas.ler_historico_compra(self.ler('historico.html'))
        self.assertTrue(h and h[0]['numero'] and h[0]['movimento'])
        doc = telas.ler_doc_compra(self.ler('documento.html'), d['item'], d['tamanho'])
        self.assertTrue(doc['emissao'] and doc['cnpjFornecedor'] and doc['itemNaNota'])
        self.assertIsNotNone(doc['custoUnitario'])
        self.assertTrue(telas.ler_lista_defeitos(self.ler('lista.html')))
        self.assertEqual(telas.campos_filtro(self.ler('filtro.html'), d['item'], date(2026, 11, 9), d['item'].split('.')[0])['action'], 'setFilterDefeito')


if __name__ == '__main__':
    unittest.main()
