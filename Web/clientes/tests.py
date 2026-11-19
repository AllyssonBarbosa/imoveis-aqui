"""Testes da W12 — etapa 1: cliente (critério 2).

Cliente cadastrado com documento e contato, sem acesso ao sistema,
isolado por empresa e acessível ao gestor e ao corretor.
"""

from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from contas.models import Usuario
from imoveis.tests import criar_corretor, criar_empresa, criar_gestor

from .models import Cliente

CPF_VALIDO = "529.982.247-25"
OUTRO_CPF_VALIDO = "176.626.330-55"
CNPJ_VALIDO = "11.444.777/0001-61"


def criar_cliente(empresa, documento=CPF_VALIDO, nome="Maria Compradora", **extra):
    dados = dict(
        empresa=empresa, tipo_pessoa=Cliente.TipoPessoa.FISICA, nome_razao_social=nome,
        documento=documento, telefone="11977776666",
    )
    dados.update(extra)
    cliente = Cliente(**dados)
    cliente.full_clean()
    cliente.save()
    return cliente


def dados_formulario(**extra):
    dados = {
        "tipo_pessoa": "FISICA", "nome_razao_social": "João Locatário", "documento": CPF_VALIDO,
        "telefone": "11955554444", "email": "", "observacoes": "",
    }
    dados.update(extra)
    return dados


class ClienteBase(TestCase):
    """Empresa A (gestor + corretor), empresa B (corretor) e o administrador."""

    def setUp(self):
        self.empresa = criar_empresa()
        self.gestor = criar_gestor(self.empresa)
        self.corretor = criar_corretor(self.empresa)
        self.outra = criar_empresa(nome="Outra Imobiliária", cnpj="11.444.777/0001-61")
        self.corretor_b = criar_corretor(self.outra, email="corretor@outra.com")
        self.admin = Usuario.objects.create_superuser(
            email="admin@teste.com", senha="senha-forte-123", nome="Admin"
        )


class ClienteModelTests(ClienteBase):
    def test_pf_com_cpf_valido_e_aceito_e_guardado_so_com_digitos(self):
        cliente = criar_cliente(self.empresa, documento=CPF_VALIDO)
        self.assertEqual(cliente.documento, "52998224725")
        self.assertEqual(cliente.documento_formatado, "529.982.247-25")

    def test_pf_com_cpf_invalido_e_recusado(self):
        with self.assertRaises(ValidationError) as erro:
            criar_cliente(self.empresa, documento="111.111.111-11")
        self.assertIn("documento", erro.exception.message_dict)

    def test_pj_com_cnpj_valido_e_aceito(self):
        cliente = criar_cliente(
            self.empresa, documento=CNPJ_VALIDO, tipo_pessoa=Cliente.TipoPessoa.JURIDICA,
            nome="Empresa Compradora Ltda",
        )
        self.assertEqual(cliente.documento_formatado, "11.444.777/0001-61")

    def test_pj_com_cnpj_invalido_e_recusado(self):
        with self.assertRaises(ValidationError):
            criar_cliente(
                self.empresa, documento="11.444.777/0001-00", tipo_pessoa=Cliente.TipoPessoa.JURIDICA,
            )

    def test_cpf_no_lugar_de_cnpj_e_recusado(self):
        with self.assertRaises(ValidationError):
            criar_cliente(self.empresa, documento=CPF_VALIDO, tipo_pessoa=Cliente.TipoPessoa.JURIDICA)

    def test_sem_telefone_e_sem_email_e_recusado(self):
        with self.assertRaises(ValidationError) as erro:
            criar_cliente(self.empresa, telefone="", email="")
        self.assertIn("telefone ou e-mail", str(erro.exception))

    def test_so_com_email_e_aceito(self):
        cliente = criar_cliente(self.empresa, telefone="", email="maria@exemplo.com")
        self.assertEqual(cliente.email, "maria@exemplo.com")

    def test_documento_repetido_na_mesma_empresa_e_recusado(self):
        criar_cliente(self.empresa, documento=CPF_VALIDO)
        with self.assertRaises(ValidationError) as erro:
            criar_cliente(self.empresa, documento="52998224725", nome="Outro nome")
        self.assertIn("Já existe um cliente com esse documento", str(erro.exception))

    def test_banco_tambem_recusa_documento_repetido(self):
        criar_cliente(self.empresa, documento=CPF_VALIDO)
        with self.assertRaises(IntegrityError):
            Cliente.objects.create(
                empresa=self.empresa, tipo_pessoa="FISICA", nome_razao_social="X",
                documento="52998224725", telefone="1",
            )

    def test_mesmo_documento_em_empresas_diferentes_e_aceito(self):
        criar_cliente(self.empresa, documento=CPF_VALIDO)
        criar_cliente(self.outra, documento=CPF_VALIDO)
        self.assertEqual(Cliente.objects.filter(documento="52998224725").count(), 2)

    def test_cliente_nao_tem_acesso_ao_sistema(self):
        """Nenhum campo do cliente aponta para Usuario: não existe login de cliente."""
        relacionados = {
            campo.related_model for campo in Cliente._meta.get_fields() if campo.is_relation
        }
        self.assertNotIn(Usuario, relacionados)
        self.assertFalse(hasattr(Cliente, "password"))


class PainelClienteTests(ClienteBase):
    def test_anonimo_vai_para_o_login(self):
        response = self.client.get(reverse("clientes:cliente_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("contas:login"), response["Location"])

    def test_administrador_nao_acessa(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("clientes:cliente_list"))
        self.assertEqual(response.status_code, 403)

    def test_gestor_e_corretor_acessam(self):
        for usuario in (self.gestor, self.corretor):
            with self.subTest(perfil=usuario.perfil):
                self.client.force_login(usuario)
                self.assertEqual(self.client.get(reverse("clientes:cliente_list")).status_code, 200)

    def test_lista_mostra_apenas_clientes_da_propria_empresa(self):
        meu = criar_cliente(self.empresa, nome="Cliente da A")
        criar_cliente(self.outra, documento=OUTRO_CPF_VALIDO, nome="Cliente da B")
        self.client.force_login(self.corretor)
        response = self.client.get(reverse("clientes:cliente_list"))
        self.assertEqual(list(response.context["clientes"]), [meu])
        self.assertNotContains(response, "Cliente da B")

    def test_busca_por_nome_e_por_documento_com_pontuacao(self):
        maria = criar_cliente(self.empresa, nome="Maria Silva", documento=CPF_VALIDO)
        criar_cliente(self.empresa, nome="José Souza", documento=OUTRO_CPF_VALIDO)
        self.client.force_login(self.gestor)
        por_nome = self.client.get(reverse("clientes:cliente_list"), {"busca": "maria"})
        por_documento = self.client.get(reverse("clientes:cliente_list"), {"busca": "529.982"})
        self.assertEqual(list(por_nome.context["clientes"]), [maria])
        self.assertEqual(list(por_documento.context["clientes"]), [maria])

    def test_cadastra_cliente_na_empresa_do_usuario(self):
        self.client.force_login(self.corretor)
        response = self.client.post(reverse("clientes:cliente_create"), dados_formulario())
        self.assertRedirects(response, reverse("clientes:cliente_list"))
        cliente = Cliente.objects.get()
        self.assertEqual(cliente.empresa, self.empresa)
        self.assertEqual(cliente.documento, "52998224725")

    def test_formulario_mostra_erro_de_cpf_invalido(self):
        self.client.force_login(self.gestor)
        response = self.client.post(
            reverse("clientes:cliente_create"), dados_formulario(documento="123.456.789-00")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CPF inválido.")
        self.assertFalse(Cliente.objects.exists())

    def test_formulario_mostra_erro_sem_contato(self):
        self.client.force_login(self.gestor)
        response = self.client.post(reverse("clientes:cliente_create"), dados_formulario(telefone=""))
        self.assertContains(response, "Informe pelo menos um contato")
        self.assertFalse(Cliente.objects.exists())

    def test_formulario_mostra_erro_de_documento_repetido(self):
        criar_cliente(self.empresa, documento=CPF_VALIDO)
        self.client.force_login(self.gestor)
        response = self.client.post(reverse("clientes:cliente_create"), dados_formulario())
        self.assertContains(response, "Já existe um cliente com esse documento")
        self.assertEqual(Cliente.objects.count(), 1)

    def test_edita_cliente_da_propria_empresa(self):
        cliente = criar_cliente(self.empresa)
        self.client.force_login(self.gestor)
        response = self.client.post(
            reverse("clientes:cliente_update", args=[cliente.pk]),
            dados_formulario(nome_razao_social="Maria Atualizada", email="maria@exemplo.com"),
        )
        self.assertRedirects(response, reverse("clientes:cliente_list"))
        cliente.refresh_from_db()
        self.assertEqual(cliente.nome_razao_social, "Maria Atualizada")

    def test_editar_cliente_de_outra_empresa_da_404(self):
        de_fora = criar_cliente(self.outra)
        self.client.force_login(self.gestor)
        url = reverse("clientes:cliente_update", args=[de_fora.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, dados_formulario(nome_razao_social="Invadido")).status_code, 404)
        de_fora.refresh_from_db()
        self.assertEqual(de_fora.nome_razao_social, "Maria Compradora")

    def test_empresa_enviada_no_formulario_e_ignorada(self):
        self.client.force_login(self.corretor)
        self.client.post(reverse("clientes:cliente_create"), dados_formulario(empresa=self.outra.pk))
        self.assertEqual(Cliente.objects.get().empresa, self.empresa)


class ApiClienteTests(APITestCase):
    def setUp(self):
        ClienteBase.setUp(self)

    def test_anonimo_recebe_401(self):
        response = self.client.get(reverse("cliente-list"))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_administrador_recebe_403(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(reverse("cliente-list"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_lista_apenas_da_propria_empresa(self):
        meu = criar_cliente(self.empresa)
        criar_cliente(self.outra)
        for usuario in (self.gestor, self.corretor):
            with self.subTest(perfil=usuario.perfil):
                self.client.force_authenticate(usuario)
                response = self.client.get(reverse("cliente-list"))
                self.assertEqual([item["id"] for item in response.data], [meu.pk])

    def test_trocar_id_na_url_nao_abre_cliente_de_outra_empresa(self):
        de_fora = criar_cliente(self.outra)
        self.client.force_authenticate(self.gestor)
        url = reverse("cliente-detail", args=[de_fora.pk])
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)
        response = self.client.patch(url, {"nome_razao_social": "Invadido"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_cria_cliente_na_empresa_do_usuario(self):
        self.client.force_authenticate(self.corretor)
        response = self.client.post(reverse("cliente-list"), dados_formulario(), format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["documento"], "52998224725")
        self.assertEqual(response.data["documento_formatado"], "529.982.247-25")
        self.assertEqual(Cliente.objects.get().empresa, self.empresa)

    def test_documento_invalido_e_recusado(self):
        self.client.force_authenticate(self.gestor)
        response = self.client.post(
            reverse("cliente-list"), dados_formulario(documento="000.000.000-00"), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("documento", response.data)

    def test_documento_repetido_e_recusado_com_mensagem(self):
        criar_cliente(self.empresa)
        self.client.force_authenticate(self.gestor)
        response = self.client.post(reverse("cliente-list"), dados_formulario(), format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Já existe um cliente com esse documento", str(response.data))

    def test_sem_contato_e_recusado(self):
        self.client.force_authenticate(self.gestor)
        response = self.client.post(reverse("cliente-list"), dados_formulario(telefone=""), format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_busca_por_documento(self):
        maria = criar_cliente(self.empresa, documento=CPF_VALIDO)
        criar_cliente(self.empresa, documento=OUTRO_CPF_VALIDO, nome="José")
        self.client.force_authenticate(self.gestor)
        response = self.client.get(reverse("cliente-list"), {"busca": "52998"})
        self.assertEqual([item["id"] for item in response.data], [maria.pk])

    def test_nao_permite_excluir(self):
        cliente = criar_cliente(self.empresa)
        self.client.force_authenticate(self.gestor)
        response = self.client.delete(reverse("cliente-detail", args=[cliente.pk]))
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertTrue(Cliente.objects.filter(pk=cliente.pk).exists())

    def test_cliente_nao_existe_na_api_publica(self):
        """A vitrine não tem rota de cliente; nome e documento não aparecem em lugar nenhum dela."""
        criar_cliente(self.empresa, nome="Maria Sigilosa")
        self.assertEqual(self.client.get("/api/publico/clientes/").status_code, status.HTTP_404_NOT_FOUND)
