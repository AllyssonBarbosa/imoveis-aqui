from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from contas.models import Usuario
from empresas.models import Empresa

from .models import Proprietario


def criar_empresa(nome="Imobiliária Teste", cnpj="74.286.058/0001-80"):
    return Empresa.objects.create(
        tipo=Empresa.Tipo.IMOBILIARIA,
        razao_social_ou_nome=nome,
        cnpj=cnpj,
        creci="12345-J",
        whatsapp="11999999999",
        email="contato@teste.com",
    )


def criar_corretor(empresa, email="corretor@teste.com"):
    return Usuario.objects.create_user(
        email=email, senha="senha-forte-123", nome="Corretor Teste",
        perfil=Usuario.Perfil.CORRETOR, empresa=empresa,
        creci="54321-F", telefone="11988887777", whatsapp="11988887777",
    )


def criar_gestor(empresa, email="gestor@teste.com"):
    return Usuario.objects.create_user(
        email=email, senha="senha-forte-123", nome="Gestor Teste",
        perfil=Usuario.Perfil.GESTOR, empresa=empresa,
    )


class ProprietarioModelTests(TestCase):
    """Critérios 1 e 6 da W03: documento validado e único por empresa."""

    def setUp(self):
        self.empresa = criar_empresa()

    def test_pf_com_cpf_valido_e_aceito_e_normalizado(self):
        proprietario = Proprietario(
            empresa=self.empresa, tipo_pessoa=Proprietario.TipoPessoa.FISICA,
            nome_razao_social="Fulano", documento="176.626.330-55",
        )
        proprietario.full_clean()
        self.assertEqual(proprietario.documento, "17662633055")

    def test_pf_com_cpf_invalido_e_recusado(self):
        proprietario = Proprietario(
            empresa=self.empresa, tipo_pessoa=Proprietario.TipoPessoa.FISICA,
            nome_razao_social="Fulano", documento="111.111.111-11",
        )
        with self.assertRaises(ValidationError):
            proprietario.full_clean()

    def test_pj_com_cnpj_valido_e_aceito(self):
        proprietario = Proprietario(
            empresa=self.empresa, tipo_pessoa=Proprietario.TipoPessoa.JURIDICA,
            nome_razao_social="Empresa Dona", documento="19.771.776/0001-33",
        )
        proprietario.full_clean()  # não deve levantar erro

    def test_pj_com_cnpj_invalido_e_recusado(self):
        proprietario = Proprietario(
            empresa=self.empresa, tipo_pessoa=Proprietario.TipoPessoa.JURIDICA,
            nome_razao_social="Empresa Dona", documento="11.111.111/1111-11",
        )
        with self.assertRaises(ValidationError):
            proprietario.full_clean()

    def test_documento_repetido_na_mesma_empresa_e_recusado(self):
        Proprietario.objects.create(
            empresa=self.empresa, tipo_pessoa=Proprietario.TipoPessoa.FISICA,
            nome_razao_social="Fulano", documento="17662633055",
        )
        duplicado = Proprietario(
            empresa=self.empresa, tipo_pessoa=Proprietario.TipoPessoa.FISICA,
            nome_razao_social="Fulano de Novo", documento="176.626.330-55",
        )
        with self.assertRaises(ValidationError):
            duplicado.full_clean()

    def test_mesmo_documento_em_empresas_diferentes_e_permitido(self):
        Proprietario.objects.create(
            empresa=self.empresa, tipo_pessoa=Proprietario.TipoPessoa.FISICA,
            nome_razao_social="Fulano", documento="17662633055",
        )
        outra_empresa = criar_empresa(nome="Outra Empresa", cnpj="19.771.776/0001-33")
        outro = Proprietario(
            empresa=outra_empresa, tipo_pessoa=Proprietario.TipoPessoa.FISICA,
            nome_razao_social="Fulano", documento="176.626.330-55",
        )
        outro.full_clean()  # não deve levantar erro

    def test_documento_formatado_pf(self):
        proprietario = Proprietario(
            empresa=self.empresa, tipo_pessoa=Proprietario.TipoPessoa.FISICA,
            nome_razao_social="Fulano", documento="17662633055",
        )
        self.assertEqual(proprietario.documento_formatado, "176.626.330-55")


class PainelProprietarioTests(TestCase):
    """Critérios 2 e 5 da W03: painel restrito à empresa, com busca."""

    def setUp(self):
        self.empresa_a = criar_empresa(nome="Empresa A", cnpj="74.286.058/0001-80")
        self.empresa_b = criar_empresa(nome="Empresa B", cnpj="19.771.776/0001-33")
        self.corretor_a = criar_corretor(self.empresa_a, email="corretora@teste.com")
        self.corretor_b = criar_corretor(self.empresa_b, email="corretorb@teste.com")

        self.proprietario_a = Proprietario.objects.create(
            empresa=self.empresa_a, tipo_pessoa=Proprietario.TipoPessoa.FISICA,
            nome_razao_social="Ana da Empresa A", documento="17662633055",
        )
        self.proprietario_b = Proprietario.objects.create(
            empresa=self.empresa_b, tipo_pessoa=Proprietario.TipoPessoa.FISICA,
            nome_razao_social="Beto da Empresa B", documento="10902516612",
        )

    def test_administrador_sem_empresa_nao_acessa(self):
        admin = Usuario.objects.create_superuser(
            email="admin@teste.com", senha="senha-forte-123", nome="Admin"
        )
        self.client.force_login(admin)
        response = self.client.get(reverse("proprietarios:proprietario_list"))
        self.assertEqual(response.status_code, 403)

    def test_gestor_nao_acessa(self):
        """A história é do corretor — gestor não cadastra proprietário."""
        gestor = criar_gestor(self.empresa_a)
        self.client.force_login(gestor)
        response = self.client.get(reverse("proprietarios:proprietario_list"))
        self.assertEqual(response.status_code, 403)

    def test_lista_mostra_apenas_proprietarios_da_propria_empresa(self):
        self.client.force_login(self.corretor_a)
        response = self.client.get(reverse("proprietarios:proprietario_list"))
        nomes = [p.nome_razao_social for p in response.context["proprietarios"]]
        self.assertIn("Ana da Empresa A", nomes)
        self.assertNotIn("Beto da Empresa B", nomes)

    def test_busca_por_nome(self):
        self.client.force_login(self.corretor_a)
        response = self.client.get(reverse("proprietarios:proprietario_list"), {"busca": "Ana"})
        nomes = [p.nome_razao_social for p in response.context["proprietarios"]]
        self.assertEqual(nomes, ["Ana da Empresa A"])

    def test_busca_por_documento(self):
        self.client.force_login(self.corretor_a)
        response = self.client.get(
            reverse("proprietarios:proprietario_list"), {"busca": "176.626.330-55"}
        )
        nomes = [p.nome_razao_social for p in response.context["proprietarios"]]
        self.assertEqual(nomes, ["Ana da Empresa A"])

    def test_corretor_cadastra_proprietario_na_propria_empresa(self):
        self.client.force_login(self.corretor_a)
        response = self.client.post(reverse("proprietarios:proprietario_create"), {
            "tipo_pessoa": Proprietario.TipoPessoa.FISICA,
            "nome_razao_social": "Novo Proprietário",
            "documento": "587.633.296-89",
            "telefone": "11977776666",
            "email": "novo@teste.com",
            "observacoes": "",
        })
        self.assertRedirects(response, reverse("proprietarios:proprietario_list"))
        criado = Proprietario.objects.get(documento="58763329689")
        self.assertEqual(criado.empresa, self.empresa_a)

    def test_documento_invalido_nao_e_aceito_no_formulario(self):
        self.client.force_login(self.corretor_a)
        response = self.client.post(reverse("proprietarios:proprietario_create"), {
            "tipo_pessoa": Proprietario.TipoPessoa.FISICA,
            "nome_razao_social": "Novo Proprietário",
            "documento": "111.111.111-11",
            "telefone": "",
            "email": "",
            "observacoes": "",
        })
        self.assertEqual(response.status_code, 200)  # re-renderiza o form com erro
        self.assertFalse(Proprietario.objects.filter(nome_razao_social="Novo Proprietário").exists())


class ProprietarioApiTests(APITestCase):
    """Isolamento multitenant e busca também pela API (critérios 2, 5 e 6)."""

    def setUp(self):
        self.empresa_a = criar_empresa(nome="Empresa A", cnpj="74.286.058/0001-80")
        self.empresa_b = criar_empresa(nome="Empresa B", cnpj="19.771.776/0001-33")
        self.corretor_a = criar_corretor(self.empresa_a, email="corretora@teste.com")
        self.corretor_b = criar_corretor(self.empresa_b, email="corretorb@teste.com")

        self.proprietario_a = Proprietario.objects.create(
            empresa=self.empresa_a, tipo_pessoa=Proprietario.TipoPessoa.FISICA,
            nome_razao_social="Ana da Empresa A", documento="17662633055",
        )
        self.proprietario_b = Proprietario.objects.create(
            empresa=self.empresa_b, tipo_pessoa=Proprietario.TipoPessoa.FISICA,
            nome_razao_social="Beto da Empresa B", documento="10902516612",
        )

    def test_lista_apenas_proprietarios_da_propria_empresa(self):
        self.client.force_authenticate(self.corretor_a)
        response = self.client.get(reverse("proprietario-list"))
        nomes = [item["nome_razao_social"] for item in response.data]
        self.assertIn("Ana da Empresa A", nomes)
        self.assertNotIn("Beto da Empresa B", nomes)

    def test_trocar_id_na_url_nao_abre_proprietario_de_outra_empresa(self):
        self.client.force_authenticate(self.corretor_a)
        response = self.client.get(reverse("proprietario-detail", args=[self.proprietario_b.pk]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_gestor_nao_acessa_a_api(self):
        """A história é do corretor — gestor não cadastra proprietário."""
        gestor = criar_gestor(self.empresa_a, email="gestor@teste.com")
        self.client.force_authenticate(gestor)
        response = self.client.get(reverse("proprietario-list"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_criar_proprietario_com_documento_invalido_e_recusado(self):
        self.client.force_authenticate(self.corretor_a)
        response = self.client.post(reverse("proprietario-list"), {
            "tipo_pessoa": Proprietario.TipoPessoa.FISICA,
            "nome_razao_social": "Documento Ruim",
            "documento": "111.111.111-11",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_criar_proprietario_com_documento_duplicado_na_empresa_e_recusado(self):
        self.client.force_authenticate(self.corretor_a)
        response = self.client.post(reverse("proprietario-list"), {
            "tipo_pessoa": Proprietario.TipoPessoa.FISICA,
            "nome_razao_social": "Ana de Novo",
            "documento": "176.626.330-55",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_busca_via_api(self):
        self.client.force_authenticate(self.corretor_a)
        response = self.client.get(reverse("proprietario-list"), {"busca": "Ana"})
        nomes = [item["nome_razao_social"] for item in response.data]
        self.assertEqual(nomes, ["Ana da Empresa A"])
