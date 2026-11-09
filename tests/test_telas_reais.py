"""Fidelidade ao MACLE real: roda os parsers em páginas salvas do sistema de verdade.

As páginas reais têm dados de clientes e fornecedores e NUNCA vão para o git. Para rodar, aponte
MACLE_FIXTURES para a pasta onde elas estão salvas (defeito.html, item.html, historico.html, ...):

  MACLE_FIXTURES=C:\\caminho\\fixtures python -m unittest tests.test_telas_reais
"""
import os
import unittest
from datetime import date
from pathlib import Path

from defeitos.macle import pdf, telas
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
        self.assertEqual(telas.campos_filtro(self.ler('filtro.html'), d['item'], date(2026, 11, 9))['action'], 'setFilterDefeito')

    def test_nfd(self):
        paginas = sorted(PASTA.glob('nfd-item-origem*.html'))  # telas "Selecione o item de origem" salvas
        if not paginas:
            self.skipTest('nenhuma nfd-item-origem*.html em MACLE_FIXTURES')
        for arq in paginas:
            t = telas.ler_item_origem(decodificar(arq.read_bytes()))
            self.assertTrue(t['codigo'] and t['linhas'] and t['associar'] and t['confirmar'], arq.name)
        self.assertTrue(telas.ler_pendentes(self.ler('nfd-pendentes.html')))
        self.assertTrue(any(len(o['cnpj']) == 14 for o in telas.ler_transportadoras(self.ler('nfd-transporte.html'))))

    def test_danfe(self):
        arq = PASTA / 'danfe-previa.pdf'
        if not arq.exists():
            self.skipTest('danfe-previa.pdf não está em MACLE_FIXTURES')
        txt = pdf.texto_pdf(arq.read_bytes())
        self.assertTrue(pdf.itens_danfe(txt))
        self.assertIsNotNone(pdf.valor_da_nota(txt))


if __name__ == '__main__':
    unittest.main()
