from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import Empresa


class EmpresaModelTests(TestCase):
    def test_imobiliaria_precisa_de_cnpj_e_nao_pode_ter_cpf(self):
        empresa = Empresa(
            tipo=Empresa.Tipo.IMOBILIARIA,
            razao_social_ou_nome="Imobiliária X",
            cpf="111.111.111-11",
            creci="12345-J",
            whatsapp="11999999999",
            email="contato@x.com",
        )
        with self.assertRaises(ValidationError):
            empresa.full_clean()

    def test_autonomo_precisa_de_cpf_e_nao_pode_ter_cnpj(self):
        empresa = Empresa(
            tipo=Empresa.Tipo.AUTONOMO,
            razao_social_ou_nome="Corretor Y",
            cnpj="11.111.111/0001-11",
            creci="54321-F",
            whatsapp="11999999999",
            email="contato@y.com",
        )
        with self.assertRaises(ValidationError):
            empresa.full_clean()

    def test_imobiliaria_com_cnpj_e_valida(self):
        empresa = Empresa(
            tipo=Empresa.Tipo.IMOBILIARIA,
            razao_social_ou_nome="Imobiliária X",
            cnpj="74.286.058/0001-80",
            creci="12345-J",
            whatsapp="11999999999",
            email="contato@x.com",
        )
        empresa.full_clean()  # não deve levantar erro

    def test_cnpj_com_digito_verificador_invalido_e_recusado(self):
        empresa = Empresa(
            tipo=Empresa.Tipo.IMOBILIARIA,
            razao_social_ou_nome="Imobiliária X",
            cnpj="11.111.111/0001-11",
            creci="12345-J",
            whatsapp="11999999999",
            email="contato@x.com",
        )
        with self.assertRaises(ValidationError):
            empresa.full_clean()

    def test_cnpj_e_normalizado_para_somente_digitos(self):
        empresa = Empresa(
            tipo=Empresa.Tipo.IMOBILIARIA,
            razao_social_ou_nome="Imobiliária X",
            cnpj="74.286.058/0001-80",
            creci="12345-J",
            whatsapp="11999999999",
            email="contato@x.com",
        )
        empresa.full_clean()
        self.assertEqual(empresa.cnpj, "74286058000180")
