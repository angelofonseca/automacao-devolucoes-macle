import unittest

from defeitos.macle.html import decodificar


class TestDecodificar(unittest.TestCase):
    def test_mistura_utf8_e_latin1(self):
        self.assertEqual(decodificar('Calçado '.encode('utf-8') + 'Ação'.encode('latin-1')), 'Calçado Ação')

    def test_bytes_binarios_nao_quebram(self):
        # formato de UTF-8, mas inválido: surrogate, overlong e acima de U+10FFFF (aparecem dentro do PDF do DANFE)
        for b in (b'\xed\xa0\x80', b'\xe0\x80\x80', b'\xf4\x90\x80\x80'):
            self.assertEqual(decodificar(b), b.decode('latin-1'))


if __name__ == '__main__':
    unittest.main()
