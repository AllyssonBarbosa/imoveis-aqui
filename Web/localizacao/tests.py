from django.test import TestCase

from empresas.models import Empresa

from .models import Cidade


class CidadeEmpresaTests(TestCase):
    """Critério 3 da W03: cidade com estado, e a empresa marca em quais atua."""

    def test_empresa_marca_cidades_de_atuacao(self):
        cidade_sp = Cidade.objects.create(nome="São Paulo", uf="SP")
        cidade_campinas = Cidade.objects.create(nome="Campinas", uf="SP")
        empresa = Empresa.objects.create(
            tipo=Empresa.Tipo.IMOBILIARIA,
            razao_social_ou_nome="Imobiliária Teste",
            cnpj="74.286.058/0001-80",
            creci="12345-J",
            whatsapp="11999999999",
            email="contato@teste.com",
        )
        empresa.cidades_atuacao.set([cidade_sp, cidade_campinas])

        self.assertEqual(empresa.cidades_atuacao.count(), 2)
        self.assertIn(empresa, cidade_sp.empresas_atuantes.all())

    def test_cidade_e_unica_por_nome_e_uf(self):
        Cidade.objects.create(nome="São Paulo", uf="SP")
        with self.assertRaises(Exception):
            Cidade.objects.create(nome="São Paulo", uf="SP")
