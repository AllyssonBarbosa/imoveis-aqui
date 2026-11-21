import io
import shutil
import tempfile
from datetime import date
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.utils import IntegrityError
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from PIL import Image
from rest_framework import status
from rest_framework.settings import api_settings
from rest_framework.test import APITestCase

from contas.models import Usuario
from core.models import Endereco
from core.throttles import ThrottleVitrinePublica
from empresas.models import Empresa
from localizacao.api.views import CidadeAtendidaAPIView
from localizacao.models import Cidade
from proprietarios.models import Proprietario

from .api.views import (
    BairrosDaCidadeAPIView,
    CaracteristicasPublicasAPIView,
    ImovelVitrineDetalheAPIView,
    PaginacaoVitrine,
    VitrineCidadeAPIView,
    VitrineCorretorAPIView,
    VitrineEmpresaAPIView,
)
from .models import (
    Apartamento,
    Caracteristica,
    Casa,
    DocumentoImovel,
    FotoImovel,
    Imovel,
    Lote,
    Loteamento,
    Predio,
    Quadra,
    Terreno,
)


# Taxa fixa da suíte (WR-09): a real vem de VITRINE_TAXA_ANONIMA no `.env` de quem roda, e um valor baixo
# ali (é para isso que a variável existe) faria testes com dezenas de requisições receberem 429.
TAXA_ALTA_NOS_TESTES = "100000/min"


def fixar_taxa_alta(teste):
    """Pina uma taxa alta no throttle da vitrine durante o teste, para a suíte não depender do ambiente.

    Os testes de limite sobrescrevem com a taxa explícita deles (`mock.patch.object` aninhado) e, ao sair,
    voltam para esta. A leitura do settings segue testável por `get_rate()`, que ignora este patch.
    """
    patcher = mock.patch.object(ThrottleVitrinePublica, "rate", TAXA_ALTA_NOS_TESTES, create=True)
    patcher.start()
    teste.addCleanup(patcher.stop)


def criar_empresa(nome="Imobiliária Teste", cnpj="74.286.058/0001-80"):
    return Empresa.objects.create(
        tipo=Empresa.Tipo.IMOBILIARIA, razao_social_ou_nome=nome, cnpj=cnpj,
        creci="12345-J", whatsapp="11999999999", email="contato@teste.com",
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


def criar_proprietario(empresa, documento="17662633055"):
    return Proprietario.objects.create(
        empresa=empresa, tipo_pessoa=Proprietario.TipoPessoa.FISICA,
        nome_razao_social="Proprietário Teste", documento=documento,
    )


def criar_imovel(empresa, corretor, proprietario, codigo="IM001", **extra):
    dados = dict(
        empresa=empresa, codigo=codigo, titulo="Casa bonita", natureza=Imovel.Natureza.CASA,
        corretor_responsavel=corretor, proprietario=proprietario,
        finalidade=Imovel.Finalidade.VENDA, preco_venda="300000.00",
    )
    dados.update(extra)
    return Imovel.objects.create(**dados)


def gerar_imagem():
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), color="blue").save(buffer, format="PNG")
    buffer.seek(0)
    return SimpleUploadedFile("foto.png", buffer.read(), content_type="image/png")


class ImovelModelTests(TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)

    def test_corretor_responsavel_precisa_ser_da_mesma_empresa(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        corretor_de_fora = criar_corretor(outra_empresa, email="fora@teste.com")
        imovel = Imovel(
            empresa=self.empresa, codigo="IM001", titulo="Casa", natureza=Imovel.Natureza.CASA,
            corretor_responsavel=corretor_de_fora, proprietario=self.proprietario,
            finalidade=Imovel.Finalidade.VENDA, preco_venda="100000",
        )
        with self.assertRaises(ValidationError):
            imovel.full_clean(exclude=["endereco"])

    def test_responsavel_precisa_ter_perfil_corretor(self):
        gestor = criar_gestor(self.empresa)
        imovel = Imovel(
            empresa=self.empresa, codigo="IM001", titulo="Casa", natureza=Imovel.Natureza.CASA,
            corretor_responsavel=gestor, proprietario=self.proprietario,
            finalidade=Imovel.Finalidade.VENDA, preco_venda="100000",
        )
        with self.assertRaises(ValidationError):
            imovel.full_clean(exclude=["endereco"])

    def test_proprietario_precisa_ser_da_mesma_empresa(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        proprietario_de_fora = criar_proprietario(outra_empresa, documento="10902516612")
        imovel = Imovel(
            empresa=self.empresa, codigo="IM001", titulo="Casa", natureza=Imovel.Natureza.CASA,
            corretor_responsavel=self.corretor, proprietario=proprietario_de_fora,
            finalidade=Imovel.Finalidade.VENDA, preco_venda="100000",
        )
        with self.assertRaises(ValidationError):
            imovel.full_clean(exclude=["endereco"])

    def test_venda_exige_preco_de_venda(self):
        imovel = Imovel(
            empresa=self.empresa, codigo="IM001", titulo="Casa", natureza=Imovel.Natureza.CASA,
            corretor_responsavel=self.corretor, proprietario=self.proprietario,
            finalidade=Imovel.Finalidade.VENDA,
        )
        with self.assertRaises(ValidationError):
            imovel.full_clean(exclude=["endereco"])

    def test_aluguel_exige_valor_do_aluguel(self):
        imovel = Imovel(
            empresa=self.empresa, codigo="IM001", titulo="Casa", natureza=Imovel.Natureza.CASA,
            corretor_responsavel=self.corretor, proprietario=self.proprietario,
            finalidade=Imovel.Finalidade.ALUGUEL,
        )
        with self.assertRaises(ValidationError):
            imovel.full_clean(exclude=["endereco"])

    def test_venda_e_aluguel_exige_os_dois_precos(self):
        imovel = Imovel(
            empresa=self.empresa, codigo="IM001", titulo="Casa", natureza=Imovel.Natureza.CASA,
            corretor_responsavel=self.corretor, proprietario=self.proprietario,
            finalidade=Imovel.Finalidade.VENDA_E_ALUGUEL, preco_venda="100000",
        )
        with self.assertRaises(ValidationError):
            imovel.full_clean(exclude=["endereco"])

        imovel.preco_aluguel = "1500"
        imovel.full_clean(exclude=["endereco"])  # agora não deve levantar erro

    def test_codigo_duplicado_na_mesma_empresa_e_recusado(self):
        criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="IM001")
        duplicado = Imovel(
            empresa=self.empresa, codigo="IM001", titulo="Outra casa", natureza=Imovel.Natureza.CASA,
            corretor_responsavel=self.corretor, proprietario=self.proprietario,
            finalidade=Imovel.Finalidade.VENDA, preco_venda="100000",
        )
        with self.assertRaises(ValidationError):
            duplicado.full_clean(exclude=["endereco"])

    def test_mesmo_codigo_em_empresas_diferentes_e_permitido(self):
        criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="IM001")
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        outro_corretor = criar_corretor(outra_empresa, email="outro@teste.com")
        outro_proprietario = criar_proprietario(outra_empresa, documento="10902516612")
        imovel = Imovel(
            empresa=outra_empresa, codigo="IM001", titulo="Casa", natureza=Imovel.Natureza.CASA,
            corretor_responsavel=outro_corretor, proprietario=outro_proprietario,
            finalidade=Imovel.Finalidade.VENDA, preco_venda="100000",
        )
        imovel.full_clean(exclude=["endereco"])  # não deve levantar erro

    def test_nao_pode_publicar_sem_endereco(self):
        imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)
        self.assertFalse(imovel.pode_publicar())
        with self.assertRaises(ValidationError):
            imovel.publicar()
        self.assertFalse(imovel.publicado)

    def test_nao_pode_publicar_sem_coordenadas(self):
        cidade = Cidade.objects.create(nome="São Paulo", uf="SP")
        endereco = Endereco.objects.create(logradouro="Rua A", cidade=cidade)
        imovel = criar_imovel(self.empresa, self.corretor, self.proprietario, endereco=endereco)
        self.assertFalse(imovel.pode_publicar())
        with self.assertRaises(ValidationError):
            imovel.publicar()

    def test_publica_com_endereco_e_coordenadas_completos(self):
        cidade = Cidade.objects.create(nome="São Paulo", uf="SP")
        endereco = Endereco.objects.create(
            cep="01000-000", logradouro="Rua A", numero="10", bairro="Centro", cidade=cidade,
            latitude="-23.55", longitude="-46.63",
        )
        imovel = criar_imovel(self.empresa, self.corretor, self.proprietario, endereco=endereco)
        self.assertTrue(imovel.pode_publicar())
        imovel.publicar()
        self.assertTrue(imovel.publicado)


class FotoImovelModelTests(TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)

    def test_apenas_uma_foto_capa_por_imovel(self):
        FotoImovel.objects.create(imovel=self.imovel, imagem=gerar_imagem(), ordem=0, capa=True)
        with self.assertRaises(IntegrityError):
            FotoImovel.objects.create(imovel=self.imovel, imagem=gerar_imagem(), ordem=1, capa=True)

    def test_definir_como_capa_desmarca_a_anterior(self):
        primeira = FotoImovel.objects.create(imovel=self.imovel, imagem=gerar_imagem(), ordem=0, capa=True)
        segunda = FotoImovel.objects.create(imovel=self.imovel, imagem=gerar_imagem(), ordem=1)

        segunda.definir_como_capa()

        primeira.refresh_from_db()
        segunda.refresh_from_db()
        self.assertFalse(primeira.capa)
        self.assertTrue(segunda.capa)


class PainelImovelTests(TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.cidade = Cidade.objects.create(nome="São Paulo", uf="SP")

    def test_administrador_nao_acessa(self):
        admin = Usuario.objects.create_superuser(
            email="admin@teste.com", senha="senha-forte-123", nome="Admin"
        )
        self.client.force_login(admin)
        response = self.client.get(reverse("imoveis:imovel_list"))
        self.assertEqual(response.status_code, 403)

    def test_gestor_nao_acessa(self):
        gestor = criar_gestor(self.empresa)
        self.client.force_login(gestor)
        response = self.client.get(reverse("imoveis:imovel_list"))
        self.assertEqual(response.status_code, 403)

    def test_corretor_cadastra_imovel(self):
        self.client.force_login(self.corretor)
        response = self.client.post(reverse("imoveis:imovel_create"), {
            "codigo": "IM001", "titulo": "Casa bonita", "natureza": Imovel.Natureza.CASA,
            "corretor_responsavel": self.corretor.pk, "proprietario": self.proprietario.pk,
            "finalidade": Imovel.Finalidade.VENDA, "preco_venda": "300000",
            "preco_aluguel": "", "preco_condominio": "", "preco_iptu": "", "descricao": "",
        })
        imovel = Imovel.objects.get(codigo="IM001")
        self.assertRedirects(response, reverse("imoveis:imovel_detail", args=[imovel.pk]))
        self.assertEqual(imovel.empresa, self.empresa)

    def test_lista_mostra_apenas_imoveis_da_propria_empresa(self):
        criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="IM001")
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        outro_corretor = criar_corretor(outra_empresa, email="outro@teste.com")
        outro_proprietario = criar_proprietario(outra_empresa, documento="10902516612")
        criar_imovel(outra_empresa, outro_corretor, outro_proprietario, codigo="IM002")

        self.client.force_login(self.corretor)
        response = self.client.get(reverse("imoveis:imovel_list"))
        codigos = [i.codigo for i in response.context["imoveis"]]
        self.assertEqual(codigos, ["IM001"])

    def test_atualizar_endereco_e_publicar(self):
        imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)
        self.client.force_login(self.corretor)

        self.client.post(reverse("imoveis:imovel_atualizar_endereco", args=[imovel.pk]), {
            "cep": "01000-000", "logradouro": "Rua A", "numero": "10", "complemento": "",
            "bairro": "Centro", "cidade": self.cidade.pk, "latitude": "-23.55", "longitude": "-46.63",
        })
        imovel.refresh_from_db()
        self.assertIsNotNone(imovel.endereco)
        self.assertIsNotNone(imovel.endereco.latitude)

        self.client.force_login(criar_gestor(self.empresa))
        self.client.post(reverse("imoveis:gestao_imovel_publicar", args=[imovel.pk]))
        imovel.refresh_from_db()
        self.assertTrue(imovel.publicado)

    def test_publicar_falha_sem_endereco(self):
        imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)
        self.client.force_login(criar_gestor(self.empresa))
        self.client.post(reverse("imoveis:gestao_imovel_publicar", args=[imovel.pk]))
        imovel.refresh_from_db()
        self.assertFalse(imovel.publicado)

    def test_upload_e_definir_capa(self):
        imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)
        self.client.force_login(self.corretor)

        self.client.post(
            reverse("imoveis:imovel_upload_fotos", args=[imovel.pk]),
            {"fotos": [gerar_imagem(), gerar_imagem()]},
            format="multipart",
        )
        self.assertEqual(imovel.fotos.count(), 2)

        primeira_foto = imovel.fotos.first()
        self.client.post(reverse("imoveis:imovel_definir_capa", args=[imovel.pk, primeira_foto.pk]))
        primeira_foto.refresh_from_db()
        self.assertTrue(primeira_foto.capa)


class ImovelApiTests(APITestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.cidade = Cidade.objects.create(nome="São Paulo", uf="SP")

        self.outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        self.outro_corretor = criar_corretor(self.outra_empresa, email="outro@teste.com")
        self.outro_proprietario = criar_proprietario(self.outra_empresa, documento="10902516612")
        self.imovel_de_fora = criar_imovel(
            self.outra_empresa, self.outro_corretor, self.outro_proprietario, codigo="FORA"
        )

    def test_gestor_nao_acessa_api(self):
        gestor = criar_gestor(self.empresa)
        self.client.force_authenticate(gestor)
        response = self.client.get(reverse("imovel-list"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_lista_apenas_da_propria_empresa(self):
        criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="IM001")
        self.client.force_authenticate(self.corretor)
        response = self.client.get(reverse("imovel-list"))
        codigos = [item["codigo"] for item in response.data["results"]]
        self.assertEqual(codigos, ["IM001"])

    def test_trocar_id_na_url_nao_abre_imovel_de_outra_empresa(self):
        self.client.force_authenticate(self.corretor)
        response = self.client.get(reverse("imovel-detail", args=[self.imovel_de_fora.pk]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_criar_imovel_valido(self):
        self.client.force_authenticate(self.corretor)
        response = self.client.post(reverse("imovel-list"), {
            "codigo": "IM001", "titulo": "Casa bonita", "natureza": Imovel.Natureza.CASA,
            "corretor_responsavel": self.corretor.pk, "proprietario": self.proprietario.pk,
            "finalidade": Imovel.Finalidade.VENDA, "preco_venda": "300000",
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_criar_imovel_venda_sem_preco_e_recusado(self):
        self.client.force_authenticate(self.corretor)
        response = self.client.post(reverse("imovel-list"), {
            "codigo": "IM001", "titulo": "Casa bonita", "natureza": Imovel.Natureza.CASA,
            "corretor_responsavel": self.corretor.pk, "proprietario": self.proprietario.pk,
            "finalidade": Imovel.Finalidade.VENDA,
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_endereco_via_api_e_publicar(self):
        imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)
        self.client.force_authenticate(self.corretor)

        url_endereco = reverse("imovel-endereco", args=[imovel.pk])
        response = self.client.put(url_endereco, {
            "cep": "01000-000", "logradouro": "Rua A", "numero": "10", "bairro": "Centro",
            "cidade": self.cidade.pk, "latitude": "-23.55", "longitude": "-46.63",
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        self.client.force_authenticate(criar_gestor(self.empresa))
        url_publicar = reverse("gestao-imovel-publicar", args=[imovel.pk])
        response = self.client.post(url_publicar)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["publicado"])

    def test_publicar_sem_endereco_e_recusado(self):
        imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)
        self.client.force_authenticate(criar_gestor(self.empresa))
        response = self.client.post(reverse("gestao-imovel-publicar", args=[imovel.pk]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_de_varias_fotos_de_uma_vez(self):
        imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)
        self.client.force_authenticate(self.corretor)

        response = self.client.post(reverse("foto-imovel-list"), {
            "imovel": imovel.pk,
            "imagens": [gerar_imagem(), gerar_imagem(), gerar_imagem()],
        }, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data), 3)
        self.assertEqual(imovel.fotos.count(), 3)

    def test_definir_capa_via_api(self):
        imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)
        foto = FotoImovel.objects.create(imovel=imovel, imagem=gerar_imagem(), ordem=0)
        self.client.force_authenticate(self.corretor)

        response = self.client.post(reverse("foto-imovel-definir-capa", args=[foto.pk]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        foto.refresh_from_db()
        self.assertTrue(foto.capa)

    def test_nao_ve_foto_de_imovel_de_outra_empresa(self):
        foto_de_fora = FotoImovel.objects.create(
            imovel=self.imovel_de_fora, imagem=gerar_imagem(), ordem=0
        )
        self.client.force_authenticate(self.corretor)
        response = self.client.get(reverse("foto-imovel-detail", args=[foto_de_fora.pk]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


DADOS_CASA = {
    "area_terreno": "300.00", "area_construida": "180.00", "quartos": 3, "suites": 1,
    "banheiros": 2, "salas": 1, "vagas": 2, "pavimentos": 2, "ano_construcao": 2015,
    "piscina": True, "quintal": True, "churrasqueira": False, "mobiliada": False,
}

DADOS_TERRENO = {
    "area_total": "360.00", "frente": "12.00", "fundo": "12.00",
    "lateral_esquerda": "30.00", "lateral_direita": "30.00",
    "topografia": Terreno.Topografia.PLANO, "murado": True, "esquina": False,
    "uso_permitido": Terreno.UsoPermitido.RESIDENCIAL,
}


def dados_de_formulario(dados):
    # checkbox desmarcado não vai no POST
    return {campo: valor for campo, valor in dados.items() if valor is not False}


class CasaTerrenoModelTests(TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.casa = criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="CASA")
        self.terreno = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="TER",
            natureza=Imovel.Natureza.TERRENO,
        )

    def test_area_livre_e_calculada(self):
        casa = Casa(imovel=self.casa, **DADOS_CASA)
        casa.full_clean()
        casa.save()
        casa.refresh_from_db()
        self.assertEqual(casa.area_livre, Decimal("120.00"))

    def test_area_livre_acompanha_mudanca_da_area_construida(self):
        casa = Casa.objects.create(imovel=self.casa, **DADOS_CASA)
        casa.refresh_from_db()
        casa.area_construida = Decimal("250.00")
        self.assertEqual(casa.area_livre, Decimal("50.00"))

    def test_area_livre_nao_e_campo_do_banco(self):
        nomes = [campo.name for campo in Casa._meta.get_fields()]
        self.assertNotIn("area_livre", nomes)

    def test_area_construida_maior_que_terreno_e_recusada_com_mensagem_clara(self):
        casa = Casa(imovel=self.casa, **{**DADOS_CASA, "area_construida": "350.00"})
        with self.assertRaises(ValidationError) as contexto:
            casa.full_clean()
        mensagem = contexto.exception.message_dict["area_construida"][0]
        self.assertIn("não pode ser maior que a área do terreno", mensagem)

    def test_area_construida_igual_ao_terreno_e_aceita(self):
        casa = Casa(imovel=self.casa, **{**DADOS_CASA, "area_construida": "300.00"})
        casa.full_clean()

    def test_banco_tambem_recusa_area_construida_maior(self):
        with self.assertRaises(IntegrityError):
            Casa.objects.create(imovel=self.casa, **{**DADOS_CASA, "area_construida": "350.00"})

    def test_suites_nao_passam_de_quartos(self):
        casa = Casa(imovel=self.casa, **{**DADOS_CASA, "quartos": 1, "suites": 2})
        with self.assertRaises(ValidationError):
            casa.full_clean()

    def test_dados_de_casa_nao_entram_em_terreno(self):
        casa = Casa(imovel=self.terreno, **DADOS_CASA)
        with self.assertRaises(ValidationError):
            casa.full_clean()

    def test_dados_de_terreno_nao_entram_em_casa(self):
        terreno = Terreno(imovel=self.casa, **DADOS_TERRENO)
        with self.assertRaises(ValidationError):
            terreno.full_clean()

    def test_cadastro_de_terreno(self):
        terreno = Terreno(imovel=self.terreno, **DADOS_TERRENO)
        terreno.full_clean()
        terreno.save()
        self.assertEqual(self.terreno.detalhes, terreno)


class PainelCasaTerrenoTests(TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.casa = criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="CASA")
        self.terreno = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="TER",
            natureza=Imovel.Natureza.TERRENO,
        )
        self.client.force_login(self.corretor)

    def test_tela_da_casa_mostra_so_campos_de_casa(self):
        response = self.client.get(reverse("imoveis:imovel_detail", args=[self.casa.pk]))
        campos = set(response.context["detalhes_form"].fields)
        self.assertIn("area_construida", campos)
        self.assertNotIn("topografia", campos)
        self.assertNotIn("area_livre", campos)
        self.assertNotContains(response, 'name="topografia"')

    def test_tela_sem_dados_ainda_mostra_que_falta_preencher(self):
        response = self.client.get(reverse("imoveis:imovel_detail", args=[self.casa.pk]))
        self.assertContains(response, "Ainda não preenchido")
        self.assertNotContains(response, "None m²")

    def test_tela_do_terreno_mostra_so_campos_de_terreno(self):
        response = self.client.get(reverse("imoveis:imovel_detail", args=[self.terreno.pk]))
        campos = set(response.context["detalhes_form"].fields)
        self.assertIn("topografia", campos)
        self.assertNotIn("quartos", campos)
        self.assertNotContains(response, 'name="quartos"')

    def test_salvar_dados_da_casa(self):
        response = self.client.post(
            reverse("imoveis:imovel_atualizar_detalhes", args=[self.casa.pk]),
            dados_de_formulario(DADOS_CASA),
        )
        self.assertRedirects(response, reverse("imoveis:imovel_detail", args=[self.casa.pk]))
        self.assertEqual(Casa.objects.get(imovel=self.casa).area_livre, Decimal("120.00"))

    def test_area_livre_digitada_e_ignorada(self):
        self.client.post(
            reverse("imoveis:imovel_atualizar_detalhes", args=[self.casa.pk]),
            {**dados_de_formulario(DADOS_CASA), "area_livre": "999"},
        )
        self.assertEqual(Casa.objects.get(imovel=self.casa).area_livre, Decimal("120.00"))

    def test_area_construida_maior_mostra_erro_na_tela(self):
        response = self.client.post(
            reverse("imoveis:imovel_atualizar_detalhes", args=[self.casa.pk]),
            {**dados_de_formulario(DADOS_CASA), "area_construida": "350"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "não pode ser maior que a área do terreno")
        self.assertFalse(Casa.objects.filter(imovel=self.casa).exists())

    def test_salvar_dados_do_terreno(self):
        self.client.post(
            reverse("imoveis:imovel_atualizar_detalhes", args=[self.terreno.pk]),
            dados_de_formulario(DADOS_TERRENO),
        )
        terreno = Terreno.objects.get(imovel=self.terreno)
        self.assertEqual(terreno.topografia, Terreno.Topografia.PLANO)
        self.assertTrue(terreno.murado)

    def test_nao_salva_detalhes_de_imovel_de_outra_empresa(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        outro_corretor = criar_corretor(outra_empresa, email="outro@teste.com")
        outro_proprietario = criar_proprietario(outra_empresa, documento="10902516612")
        de_fora = criar_imovel(outra_empresa, outro_corretor, outro_proprietario, codigo="FORA")
        response = self.client.post(
            reverse("imoveis:imovel_atualizar_detalhes", args=[de_fora.pk]),
            dados_de_formulario(DADOS_CASA),
        )
        self.assertEqual(response.status_code, 404)


class ApiCasaTerrenoTests(APITestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.casa = criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="CASA")
        self.terreno = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="TER",
            natureza=Imovel.Natureza.TERRENO,
        )
        self.client.force_authenticate(self.corretor)

    def test_detalhe_da_casa_traz_dados_e_area_livre(self):
        Casa.objects.create(imovel=self.casa, **DADOS_CASA)
        response = self.client.get(reverse("imovel-detail", args=[self.casa.pk]))
        self.assertEqual(response.data["natureza"], "CASA")
        self.assertEqual(response.data["casa"]["quartos"], 3)
        self.assertEqual(response.data["casa"]["area_livre"], "120.00")
        self.assertIsNone(response.data["terreno"])

    def test_detalhe_do_terreno_traz_dados(self):
        Terreno.objects.create(imovel=self.terreno, **DADOS_TERRENO)
        response = self.client.get(reverse("imovel-detail", args=[self.terreno.pk]))
        self.assertEqual(response.data["terreno"]["topografia"], "PLANO")
        self.assertIsNone(response.data["casa"])

    def test_put_detalhes_da_casa(self):
        response = self.client.put(
            reverse("imovel-detalhes", args=[self.casa.pk]), DADOS_CASA, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["area_livre"], "120.00")

    def test_area_livre_enviada_pela_api_e_ignorada(self):
        response = self.client.put(
            reverse("imovel-detalhes", args=[self.casa.pk]),
            {**DADOS_CASA, "area_livre": "999.00"}, format="json",
        )
        self.assertEqual(response.data["area_livre"], "120.00")

    def test_put_area_construida_maior_e_recusado(self):
        response = self.client.put(
            reverse("imovel-detalhes", args=[self.casa.pk]),
            {**DADOS_CASA, "area_construida": "350.00"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("não pode ser maior", str(response.data["area_construida"][0]))

    def test_put_detalhes_do_terreno_usa_campos_de_terreno(self):
        response = self.client.put(
            reverse("imovel-detalhes", args=[self.terreno.pk]), DADOS_CASA, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("topografia", response.data)

    def test_natureza_nao_pode_ser_trocada(self):
        response = self.client.patch(
            reverse("imovel-detail", args=[self.casa.pk]),
            {"natureza": Imovel.Natureza.TERRENO}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_detalhes_de_outra_empresa_da_404(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        outro_corretor = criar_corretor(outra_empresa, email="outro@teste.com")
        outro_proprietario = criar_proprietario(outra_empresa, documento="10902516612")
        de_fora = criar_imovel(outra_empresa, outro_corretor, outro_proprietario, codigo="FORA")
        response = self.client.put(
            reverse("imovel-detalhes", args=[de_fora.pk]), DADOS_CASA, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


def criar_predio(empresa, nome="Edifício Aurora", cidade=None, **extra):
    cidade = cidade or Cidade.objects.get_or_create(nome="São Paulo", uf="SP")[0]
    endereco = Endereco.objects.create(
        cep="01310-100", logradouro="Av. Paulista", numero="1000", bairro="Bela Vista", cidade=cidade,
        latitude="-23.56", longitude="-46.65",
    )
    dados = dict(
        empresa=empresa, nome=nome, endereco=endereco, andares=10, unidades_por_andar=4,
        ano_construcao=2010, elevadores=2, portaria_24h=True, piscina=True,
        valor_condominio_base="800.00",
    )
    dados.update(extra)
    return Predio.objects.create(**dados)


def dados_apartamento(predio, **extra):
    dados = {
        "predio": predio.pk, "andar": 3, "numero": "31", "area_privativa": "70.00",
        "area_total": "90.00", "quartos": 2, "suites": 1, "banheiros": 2, "vagas": 1,
        "varanda": True, "posicao_solar": Apartamento.PosicaoSolar.NASCENTE,
    }
    dados.update(extra)
    return dados


def criar_apartamento(imovel, predio, **extra):
    dados = dados_apartamento(predio, **extra)
    dados["predio"] = predio
    return Apartamento.objects.create(imovel=imovel, **dados)


class PredioApartamentoModelTests(TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.predio = criar_predio(self.empresa)
        self.apto = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="AP1",
            natureza=Imovel.Natureza.APARTAMENTO,
        )
        self.apto2 = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="AP2",
            natureza=Imovel.Natureza.APARTAMENTO,
        )

    def _apartamento(self, imovel, **extra):
        dados = dados_apartamento(self.predio, **extra)
        dados["predio"] = self.predio
        return Apartamento(imovel=imovel, **dados)

    def test_cadastro_de_apartamento_ligado_ao_predio(self):
        apartamento = self._apartamento(self.apto)
        apartamento.full_clean()
        apartamento.save()
        self.assertEqual(self.predio.apartamentos.count(), 1)

    def test_mesmo_andar_e_numero_no_mesmo_predio_e_recusado(self):
        criar_apartamento(self.apto, self.predio)
        duplicado = self._apartamento(self.apto2)
        with self.assertRaises(ValidationError) as contexto:
            duplicado.full_clean()
        self.assertIn(
            "Já existe um apartamento com esse andar e número neste prédio.",
            contexto.exception.messages,
        )

    def test_banco_tambem_recusa_andar_e_numero_repetidos(self):
        criar_apartamento(self.apto, self.predio)
        with self.assertRaises(IntegrityError):
            criar_apartamento(self.apto2, self.predio)

    def test_mesmo_numero_em_outro_andar_e_aceito(self):
        criar_apartamento(self.apto, self.predio)
        self._apartamento(self.apto2, andar=4).full_clean()

    def test_mesmo_andar_e_numero_em_outro_predio_e_aceito(self):
        criar_apartamento(self.apto, self.predio)
        outro_predio = criar_predio(self.empresa, nome="Edifício Boreal")
        apartamento = self._apartamento(self.apto2)
        apartamento.predio = outro_predio
        apartamento.full_clean()

    def test_andar_acima_do_predio_e_recusado(self):
        with self.assertRaises(ValidationError) as contexto:
            self._apartamento(self.apto, andar=11).full_clean()
        self.assertIn("andar", contexto.exception.message_dict)

    def test_area_privativa_maior_que_total_e_recusada(self):
        with self.assertRaises(ValidationError) as contexto:
            self._apartamento(self.apto, area_privativa="100.00").full_clean()
        self.assertIn("area_privativa", contexto.exception.message_dict)

    def test_predio_de_outra_empresa_e_recusado(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        predio_de_fora = criar_predio(outra_empresa)
        apartamento = self._apartamento(self.apto)
        apartamento.predio = predio_de_fora
        with self.assertRaises(ValidationError) as contexto:
            apartamento.full_clean()
        self.assertIn("predio", contexto.exception.message_dict)

    def test_apartamento_nao_entra_em_casa(self):
        casa = criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="CASA")
        with self.assertRaises(ValidationError):
            self._apartamento(casa).full_clean()

    def test_nome_de_predio_repetido_na_empresa_e_recusado(self):
        repetido = Predio(
            empresa=self.empresa, nome="Edifício Aurora", andares=5, unidades_por_andar=2,
        )
        with self.assertRaises(ValidationError):
            repetido.full_clean(exclude=["endereco"])

    def test_apartamento_usa_endereco_do_predio_para_publicar(self):
        criar_apartamento(self.apto, self.predio)
        self.assertEqual(self.apto.endereco_do_anuncio, self.predio.endereco)
        self.assertTrue(self.apto.pode_publicar())

    def test_apartamento_sem_predio_ainda_nao_publica(self):
        self.assertFalse(self.apto.pode_publicar())

    def test_alterar_predio_reflete_em_todas_as_unidades(self):
        criar_apartamento(self.apto, self.predio)
        criar_apartamento(self.apto2, self.predio, numero="32")

        self.predio.academia = True
        self.predio.save()

        for imovel in (self.apto, self.apto2):
            imovel = Imovel.objects.get(pk=imovel.pk)
            self.assertTrue(imovel.detalhes.predio.academia)


class PainelPredioApartamentoTests(TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.cidade = Cidade.objects.create(nome="São Paulo", uf="SP")
        self.predio = criar_predio(self.empresa, cidade=self.cidade)
        self.apto = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="AP1",
            natureza=Imovel.Natureza.APARTAMENTO,
        )
        self.client.force_login(self.corretor)

    def _post_predio(self, url, **extra):
        dados = {
            "nome": "Edifício Novo", "andares": 8, "unidades_por_andar": 2,
            "ano_construcao": 2018, "elevadores": 1, "portaria_24h": "on", "piscina": "on",
            "valor_condominio_base": "650.00",
            "endereco-logradouro": "Rua B", "endereco-numero": "20", "endereco-cep": "",
            "endereco-complemento": "", "endereco-bairro": "Centro",
            "endereco-cidade": self.cidade.pk, "endereco-latitude": "-23.5",
            "endereco-longitude": "-46.6",
        }
        dados.update(extra)
        return self.client.post(url, dados)

    def test_gestor_nao_acessa_predios(self):
        self.client.force_login(criar_gestor(self.empresa))
        response = self.client.get(reverse("imoveis:predio_list"))
        self.assertEqual(response.status_code, 403)

    def test_cadastra_predio_com_endereco(self):
        response = self._post_predio(reverse("imoveis:predio_create"))
        self.assertRedirects(response, reverse("imoveis:predio_list"))
        predio = Predio.objects.get(nome="Edifício Novo")
        self.assertEqual(predio.empresa, self.empresa)
        self.assertEqual(predio.endereco.logradouro, "Rua B")
        self.assertTrue(predio.portaria_24h)

    def test_nome_repetido_mostra_erro(self):
        response = self._post_predio(reverse("imoveis:predio_create"), nome="Edifício Aurora")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Já existe um prédio com esse nome")

    def test_lista_so_predios_da_propria_empresa(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        criar_predio(outra_empresa, nome="Prédio de fora")
        response = self.client.get(reverse("imoveis:predio_list"))
        nomes = [p.nome for p in response.context["predios"]]
        self.assertEqual(nomes, ["Edifício Aurora"])

    def test_editar_predio_de_outra_empresa_da_404(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        de_fora = criar_predio(outra_empresa, nome="Prédio de fora")
        response = self.client.get(reverse("imoveis:predio_update", args=[de_fora.pk]))
        self.assertEqual(response.status_code, 404)

    def test_editar_predio_reflete_no_detalhe_do_apartamento(self):
        criar_apartamento(self.apto, self.predio)
        self._post_predio(
            reverse("imoveis:predio_update", args=[self.predio.pk]),
            nome="Edifício Aurora", academia="on", **{"endereco-logradouro": "Rua Corrigida"},
        )
        response = self.client.get(reverse("imoveis:imovel_detail", args=[self.apto.pk]))
        self.assertContains(response, "Academia")
        self.assertContains(response, "Rua Corrigida")
        self.assertEqual(Endereco.objects.filter(logradouro="Rua Corrigida").count(), 1)

    def test_tela_do_apartamento_so_lista_predios_da_empresa(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        criar_predio(outra_empresa, nome="Prédio de fora")
        response = self.client.get(reverse("imoveis:imovel_detail", args=[self.apto.pk]))
        form = response.context["detalhes_form"]
        self.assertEqual(list(form.fields["predio"].queryset), [self.predio])
        self.assertNotIn("area_terreno", form.fields)

    def test_salva_apartamento_e_detalhe_mostra_dados_do_predio(self):
        dados = dados_de_formulario(dados_apartamento(self.predio))
        response = self.client.post(
            reverse("imoveis:imovel_atualizar_detalhes", args=[self.apto.pk]), dados
        )
        self.assertRedirects(response, reverse("imoveis:imovel_detail", args=[self.apto.pk]))
        response = self.client.get(reverse("imoveis:imovel_detail", args=[self.apto.pk]))
        self.assertContains(response, "Edifício Aurora")
        self.assertContains(response, "Portaria 24h")
        self.assertContains(response, "Av. Paulista")

    def test_apartamento_repetido_mostra_mensagem_clara(self):
        outro = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="AP2",
            natureza=Imovel.Natureza.APARTAMENTO,
        )
        criar_apartamento(outro, self.predio)
        response = self.client.post(
            reverse("imoveis:imovel_atualizar_detalhes", args=[self.apto.pk]),
            dados_de_formulario(dados_apartamento(self.predio)),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Já existe um apartamento com esse andar e número neste prédio.")

    def test_apartamento_nao_tem_endereco_proprio(self):
        self.client.post(reverse("imoveis:imovel_atualizar_endereco", args=[self.apto.pk]), {
            "logradouro": "Rua X", "cidade": self.cidade.pk,
        })
        self.apto.refresh_from_db()
        self.assertIsNone(self.apto.endereco)


class ApiPredioApartamentoTests(APITestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.cidade = Cidade.objects.create(nome="São Paulo", uf="SP")
        self.predio = criar_predio(self.empresa, cidade=self.cidade)
        self.apto = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="AP1",
            natureza=Imovel.Natureza.APARTAMENTO,
        )
        self.outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        self.predio_de_fora = criar_predio(self.outra_empresa, nome="Prédio de fora")
        self.client.force_authenticate(self.corretor)

    def test_cria_predio_com_endereco(self):
        response = self.client.post(reverse("predio-list"), {
            "nome": "Edifício Novo", "andares": 5, "unidades_por_andar": 2, "elevadores": 1,
            "academia": True,
            "endereco": {"logradouro": "Rua B", "cidade": self.cidade.pk},
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        predio = Predio.objects.get(pk=response.data["id"])
        self.assertEqual(predio.empresa, self.empresa)
        self.assertEqual(predio.endereco.logradouro, "Rua B")

    def test_nome_repetido_e_recusado(self):
        response = self.client.post(reverse("predio-list"), {
            "nome": "Edifício Aurora", "andares": 5, "unidades_por_andar": 2,
            "endereco": {"logradouro": "Rua B", "cidade": self.cidade.pk},
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_lista_so_predios_da_propria_empresa(self):
        response = self.client.get(reverse("predio-list"))
        self.assertEqual([p["nome"] for p in response.data], ["Edifício Aurora"])

    def test_predio_de_outra_empresa_da_404(self):
        response = self.client.get(reverse("predio-detail", args=[self.predio_de_fora.pk]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_put_detalhes_do_apartamento(self):
        response = self.client.put(
            reverse("imovel-detalhes", args=[self.apto.pk]), dados_apartamento(self.predio),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["predio"]["nome"], "Edifício Aurora")

    def test_detalhe_do_apartamento_traz_dados_do_predio(self):
        criar_apartamento(self.apto, self.predio)
        response = self.client.get(reverse("imovel-detail", args=[self.apto.pk]))
        apartamento = response.data["apartamento"]
        self.assertEqual(apartamento["numero"], "31")
        self.assertEqual(apartamento["predio"]["nome"], "Edifício Aurora")
        self.assertTrue(apartamento["predio"]["portaria_24h"])
        self.assertEqual(apartamento["predio"]["endereco"]["logradouro"], "Av. Paulista")
        self.assertEqual(response.data["endereco"]["logradouro"], "Av. Paulista")
        self.assertIsNone(response.data["casa"])

    def test_alterar_predio_reflete_no_detalhe_de_todos_os_apartamentos(self):
        outro = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="AP2",
            natureza=Imovel.Natureza.APARTAMENTO,
        )
        criar_apartamento(self.apto, self.predio)
        criar_apartamento(outro, self.predio, numero="32")

        response = self.client.patch(reverse("predio-detail", args=[self.predio.pk]), {
            "playground": True, "endereco": {"logradouro": "Rua Corrigida", "cidade": self.cidade.pk},
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        for imovel in (self.apto, outro):
            detalhe = self.client.get(reverse("imovel-detail", args=[imovel.pk])).data
            self.assertTrue(detalhe["apartamento"]["predio"]["playground"])
            self.assertEqual(detalhe["endereco"]["logradouro"], "Rua Corrigida")

    def test_apartamento_repetido_e_recusado_com_mensagem(self):
        outro = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="AP2",
            natureza=Imovel.Natureza.APARTAMENTO,
        )
        criar_apartamento(outro, self.predio)
        response = self.client.put(
            reverse("imovel-detalhes", args=[self.apto.pk]), dados_apartamento(self.predio),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            "Já existe um apartamento com esse andar e número neste prédio.",
            [str(m) for m in response.data["non_field_errors"]],
        )

    def test_nao_liga_apartamento_a_predio_de_outra_empresa(self):
        response = self.client.put(
            reverse("imovel-detalhes", args=[self.apto.pk]),
            dados_apartamento(self.predio_de_fora), format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("predio", response.data)

    def test_nao_exclui_predio_com_apartamentos(self):
        criar_apartamento(self.apto, self.predio)
        response = self.client.delete(reverse("predio-detail", args=[self.predio.pk]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(Predio.objects.filter(pk=self.predio.pk).exists())

    def test_endereco_proprio_do_apartamento_e_recusado(self):
        response = self.client.put(reverse("imovel-endereco", args=[self.apto.pk]), {
            "logradouro": "Rua X", "cidade": self.cidade.pk,
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# --- Vitrine pública (W09) ---------------------------------------------------

MEDIA_DE_TESTE = tempfile.mkdtemp()


def criar_endereco(cidade, bairro, **extra):
    dados = dict(
        logradouro="Rua das Flores", numero="10", complemento="Casa 2", bairro=bairro,
        cidade=cidade, latitude="-23.600000", longitude="-46.660000",
    )
    dados.update(extra)
    return Endereco.objects.create(**dados)


@override_settings(MEDIA_ROOT=MEDIA_DE_TESTE)
class VitrineBase(APITestCase):
    """Cenário: São Paulo com 4 imóveis publicados (casa, sobrado, apartamento e terreno),
    mais um rascunho, um de empresa inativa e um em Campinas — esses três não podem
    aparecer na busca de São Paulo.
    """

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(MEDIA_DE_TESTE, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        # o limite de taxa guarda o histórico no cache; cada teste começa do zero e com taxa alta fixa,
        # então a suíte não depende da velocidade da máquina nem do VITRINE_TAXA_ANONIMA do `.env`
        cache.clear()
        fixar_taxa_alta(self)
        self.sp = Cidade.objects.create(nome="São Paulo", uf="SP")
        self.campinas = Cidade.objects.create(nome="Campinas", uf="SP")
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = Proprietario.objects.create(
            empresa=self.empresa, tipo_pessoa=Proprietario.TipoPessoa.FISICA,
            nome_razao_social="Dona Secreta da Silva", documento="17662633055",
        )
        self.portao = Caracteristica.objects.create(nome="Portão eletrônico")
        self.ar = Caracteristica.objects.create(nome="Ar-condicionado")

        self.casa = self._publicado(
            "CASA-INTERNO-1", titulo="Casa com quintal", bairro="Moema",
            preco_venda="500000.00", descricao="Ótima para família.",
        )
        Casa.objects.create(
            imovel=self.casa, area_terreno="300.00", area_construida="150.00",
            quartos=3, suites=1, vagas=2, banheiros=2,
        )
        self.casa.caracteristicas.set([self.portao, self.ar])
        FotoImovel.objects.create(imovel=self.casa, imagem=gerar_imagem(), ordem=0)
        FotoImovel.objects.create(imovel=self.casa, imagem=gerar_imagem(), ordem=1, capa=True)

        self.sobrado = self._publicado(
            "CASA-INTERNO-2", titulo="Sobrado", bairro="Pinheiros",
            finalidade=Imovel.Finalidade.ALUGUEL, preco_venda=None, preco_aluguel="3000.00",
            descricao="Perto do metrô.",
        )
        Casa.objects.create(
            imovel=self.sobrado, area_terreno="120.00", area_construida="90.00",
            quartos=2, suites=0, vagas=1,
        )
        self.sobrado.caracteristicas.set([self.portao])

        # apartamento não tem endereço próprio: cidade e bairro vêm do prédio
        self.predio = criar_predio(self.empresa, cidade=self.sp)
        self.predio.endereco.bairro = "Bela Vista"
        self.predio.endereco.save()
        self.apto = self._publicado(
            "APTO-INTERNO-1", titulo="Apartamento novo", bairro=None,
            natureza=Imovel.Natureza.APARTAMENTO, finalidade=Imovel.Finalidade.VENDA_E_ALUGUEL,
            preco_venda="400000.00", preco_aluguel="2500.00",
        )
        criar_apartamento(self.apto, self.predio)  # 70 m² privativos, 2 quartos, 1 suíte, 1 vaga

        self.terreno = self._publicado(
            "TERRENO-INTERNO-1", titulo="Lote plano", bairro="Moema",
            natureza=Imovel.Natureza.TERRENO, preco_venda="200000.00",
        )
        Terreno.objects.create(
            imovel=self.terreno, area_total="360.00", frente="12.00", fundo="30.00",
            lateral_esquerda="30.00", lateral_direita="30.00",
            topografia=Terreno.Topografia.PLANO, uso_permitido=Terreno.UsoPermitido.RESIDENCIAL,
        )

        self.rascunho = self._publicado("RASCUNHO-1", titulo="Casa rascunho", bairro="Moema")
        self.rascunho.publicado = False
        self.rascunho.save(update_fields=["publicado"])

        self.em_campinas = self._publicado(
            "CAMPINAS-1", titulo="Casa em Campinas", bairro="Centro", cidade=self.campinas
        )

        empresa_inativa = criar_empresa(nome="Inativa", cnpj="11.222.333/0001-81")
        empresa_inativa.ativa = False
        empresa_inativa.save(update_fields=["ativa"])
        self.da_inativa = criar_imovel(
            empresa_inativa, criar_corretor(empresa_inativa, email="c2@teste.com"),
            criar_proprietario(empresa_inativa), codigo="INATIVA-1", titulo="Casa da inativa",
            endereco=criar_endereco(self.sp, "Moema"), publicado=True,
        )

    def _publicado(self, codigo, bairro, cidade=None, **extra):
        endereco = criar_endereco(cidade or self.sp, bairro) if bairro else None
        return criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo=codigo,
            endereco=endereco, publicado=True, **extra,
        )

    def buscar(self, cidade=None, **params):
        url = reverse("publico-vitrine-cidade", args=[(cidade or self.sp).pk])
        return self.client.get(url, params)

    def ids(self, response):
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return [item["id"] for item in response.data["results"]]


class VitrineCidadesTests(VitrineBase):
    """Critério 1: lista as cidades atendidas, com estado."""

    def test_lista_cidades_atendidas_com_estado_sem_token(self):
        Cidade.objects.create(nome="Recife", uf="PE")  # sem imóvel nenhum
        response = self.client.get(reverse("publico-cidades"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertCountEqual(
            response.data,
            [
                {"id": self.campinas.pk, "nome": "Campinas", "uf": "SP"},
                {"id": self.sp.pk, "nome": "São Paulo", "uf": "SP"},
            ],
        )

    def test_cidade_so_com_rascunho_ou_empresa_inativa_nao_e_atendida(self):
        santos = Cidade.objects.create(nome="Santos", uf="SP")
        self.rascunho.endereco.cidade = santos
        self.rascunho.endereco.save()
        self.da_inativa.endereco.cidade = santos
        self.da_inativa.endereco.save()
        response = self.client.get(reverse("publico-cidades"))
        self.assertNotIn(santos.pk, [c["id"] for c in response.data])


class VitrineLimiteDeTaxaTests(VitrineBase):
    """WR-02: limite anônimo por IP nas rotas públicas da vitrine (balde único, 429)."""

    def _rotas_publicas(self):
        return [
            reverse("publico-cidades"),
            reverse("publico-bairros-cidade", args=[self.sp.pk]),
            reverse("publico-caracteristicas"),
            reverse("publico-imovel", args=[self.casa.pk]),
        ]

    def test_visitante_anonimo_passa_do_limite_e_recebe_429(self):
        with mock.patch.object(ThrottleVitrinePublica, "rate", "3/min", create=True):
            for _ in range(3):
                self.assertEqual(self.buscar().status_code, status.HTTP_200_OK)
            response = self.buscar()
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn("Retry-After", response)
        self.assertIn("detail", response.data)

    def test_taxa_padrao_vem_do_settings(self):
        # `get_rate()` lê o settings e ignora o patch de taxa alta do setUp (que troca só `.rate`)
        esperada = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["vitrine_publica"]
        self.assertEqual(ThrottleVitrinePublica().get_rate(), esperada)
        self.assertEqual(ThrottleVitrinePublica.scope, "vitrine_publica")

    def test_taxa_fixa_da_suite_nao_depende_do_ambiente(self):
        self.assertEqual(ThrottleVitrinePublica().rate, TAXA_ALTA_NOS_TESTES)

    def test_todas_as_rotas_publicas_da_vitrine_tem_o_limite(self):
        for view in (
            VitrineCidadeAPIView, BairrosDaCidadeAPIView, CaracteristicasPublicasAPIView,
            ImovelVitrineDetalheAPIView, CidadeAtendidaAPIView,
            VitrineCorretorAPIView, VitrineEmpresaAPIView,
        ):
            with self.subTest(view=view.__name__):
                self.assertEqual(view.throttle_classes, [ThrottleVitrinePublica])

    def test_as_rotas_publicas_dividem_o_mesmo_balde(self):
        with mock.patch.object(ThrottleVitrinePublica, "rate", "4/min", create=True):
            for url in self._rotas_publicas():
                self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK, url)
            response = self.buscar()
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_x_forwarded_for_forjado_nao_reinicia_a_contagem(self):
        configuracao = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 0}
        with override_settings(REST_FRAMEWORK=configuracao), mock.patch.object(
            ThrottleVitrinePublica, "rate", "2/min", create=True
        ):
            respostas = [
                self.client.get(
                    reverse("publico-cidades"), HTTP_X_FORWARDED_FOR=f"10.0.0.{n}"
                ).status_code
                for n in (1, 2, 3)
            ]
        self.assertEqual(
            respostas,
            [status.HTTP_200_OK, status.HTTP_200_OK, status.HTTP_429_TOO_MANY_REQUESTS],
        )

    def test_api_autenticada_do_painel_nao_tem_limite(self):
        self.assertEqual(list(api_settings.DEFAULT_THROTTLE_CLASSES), [])
        self.client.force_authenticate(self.corretor)
        with mock.patch.object(ThrottleVitrinePublica, "rate", "1/min", create=True):
            for _ in range(3):
                response = self.client.get(reverse("imovel-list"))
                self.assertEqual(response.status_code, status.HTTP_200_OK)


class VitrineBuscaTests(VitrineBase):
    """Critérios 2, 3 e 4: busca dentro da cidade, filtros, ordenação e paginação."""

    def test_busca_traz_so_publicados_de_empresa_ativa_da_cidade(self):
        self.assertCountEqual(
            self.ids(self.buscar()),
            [self.casa.pk, self.sobrado.pk, self.apto.pk, self.terreno.pk],
        )
        self.assertEqual(self.ids(self.buscar(cidade=self.campinas)), [self.em_campinas.pk])

    def test_cidade_inexistente_da_404(self):
        response = self.client.get(reverse("publico-vitrine-cidade", args=[99999]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_texto_livre_procura_no_titulo_bairro_e_descricao(self):
        self.assertEqual(self.ids(self.buscar(busca="quintal")), [self.casa.pk])
        self.assertEqual(self.ids(self.buscar(busca="metrô")), [self.sobrado.pk])
        # o bairro do apartamento vem do prédio
        self.assertEqual(self.ids(self.buscar(busca="bela vista")), [self.apto.pk])

    def test_filtro_finalidade_inclui_venda_e_aluguel(self):
        self.assertCountEqual(
            self.ids(self.buscar(finalidade="VENDA")), [self.casa.pk, self.apto.pk, self.terreno.pk]
        )
        self.assertCountEqual(
            self.ids(self.buscar(finalidade="ALUGUEL")), [self.sobrado.pk, self.apto.pk]
        )

    def test_filtro_natureza(self):
        self.assertEqual(self.ids(self.buscar(natureza="APARTAMENTO")), [self.apto.pk])
        self.assertEqual(self.ids(self.buscar(natureza="TERRENO")), [self.terreno.pk])

    def test_filtro_faixa_de_preco_usa_o_preco_da_finalidade(self):
        self.assertEqual(
            self.ids(self.buscar(finalidade="VENDA", preco_min="300000", preco_max="450000")),
            [self.apto.pk],
        )
        # no aluguel, o apartamento vale 2500 — não os 400 mil da venda
        self.assertEqual(
            self.ids(self.buscar(finalidade="ALUGUEL", preco_max="2800")), [self.apto.pk]
        )

    def test_filtros_de_quartos_suites_e_vagas_sao_minimos(self):
        self.assertEqual(self.ids(self.buscar(quartos=3)), [self.casa.pk])
        self.assertCountEqual(
            self.ids(self.buscar(quartos=2)), [self.casa.pk, self.sobrado.pk, self.apto.pk]
        )
        self.assertCountEqual(self.ids(self.buscar(suites=1)), [self.casa.pk, self.apto.pk])
        self.assertEqual(self.ids(self.buscar(vagas=2)), [self.casa.pk])

    def test_filtro_bairro(self):
        self.assertCountEqual(self.ids(self.buscar(bairro="moema")), [self.casa.pk, self.terreno.pk])
        self.assertEqual(self.ids(self.buscar(bairro="Bela Vista")), [self.apto.pk])

    def test_filtro_area_usa_a_area_principal_da_natureza(self):
        # casa 150 (construída), sobrado 90, apto 70 (privativa), terreno 360 (total)
        self.assertCountEqual(self.ids(self.buscar(area_min=100)), [self.casa.pk, self.terreno.pk])
        self.assertCountEqual(self.ids(self.buscar(area_max=100)), [self.sobrado.pk, self.apto.pk])

    def test_filtro_caracteristicas_exige_todas(self):
        self.assertCountEqual(
            self.ids(self.buscar(caracteristicas=f"{self.portao.pk}")), [self.casa.pk, self.sobrado.pk]
        )
        self.assertEqual(
            self.ids(self.buscar(caracteristicas=f"{self.portao.pk},{self.ar.pk}")), [self.casa.pk]
        )

    # --- características: teto de ids, formato ASCII e uma só subconsulta (CR-01, D-13) ---

    def _ids_inexistentes(self, quantidade):
        # ids bem acima dos dois criados no setUp; ninguém tem essas características
        return [str(900000 + n) for n in range(quantidade)]

    def test_caracteristicas_demais_da_400(self):
        # 21 ids distintos: 400 antes de qualquer consulta
        ids = [str(self.portao.pk), str(self.ar.pk)] + self._ids_inexistentes(19)
        response = self.buscar(caracteristicas=",".join(ids))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["caracteristicas"], "Escolha no máximo 20 características.")
        # exatamente 20: passa (e a lista fica vazia, porque ninguém tem as 20)
        ids = [str(self.portao.pk), str(self.ar.pk)] + self._ids_inexistentes(18)
        response = self.buscar(caracteristicas=",".join(ids))
        self.assertEqual(self.ids(response), [])
        # o 400 por tamanho não chega ao banco
        ids = ",".join(self._ids_inexistentes(21))
        with CaptureQueriesContext(connection) as consultas:
            self.buscar(caracteristicas=ids)
        self.assertFalse(
            [c for c in consultas.captured_queries if "imoveis_imovel" in c["sql"]],
            "21 ids deveriam ser recusados antes de consultar os imóveis",
        )

    def test_id_de_caracteristica_fora_do_formato_da_400(self):
        portao = self.portao.pk
        valores = ["0", "-1", "1_0", "٣", "1.0", "abc", f"{portao},x", f"{portao}\x00"]
        # WR-07: dígitos demais nunca chegam ao `int()` nem ao banco (antes: ValueError/OverflowError, 500)
        valores += ["0" * 5000, "9" * 5000, "9" * 30, "0" * 19, f"{portao},{'9' * 30}"]
        for valor in valores:
            with self.subTest(valor=valor):
                response = self.buscar(caracteristicas=valor)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(
                    response.data["caracteristicas"], "Informe os ids separados por vírgula."
                )

    def test_id_de_caracteristica_comum_continua_funcionando(self):
        # o teto de 18 dígitos não atrapalha ids normais, nem zeros à esquerda dentro do teto
        portao = self.portao.pk
        for valor in (f"{portao}", f"{portao:05d}", f" {portao} "):
            with self.subTest(valor=valor):
                response = self.buscar(caracteristicas=valor)
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertCountEqual(self.ids(response), [self.casa.pk, self.sobrado.pk])
        # 18 noves é o maior id aceito: passa pelo formato e simplesmente não acha nada
        response = self.buscar(caracteristicas="9" * 18)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.ids(response), [])

    def test_ids_repetidos_e_vazios_sao_ignorados(self):
        portao = self.portao.pk
        esperado = [self.casa.pk, self.sobrado.pk]
        for valor in (f"{portao}", f"{portao},{portao},", f",{portao},,"):
            with self.subTest(valor=valor):
                self.assertCountEqual(self.ids(self.buscar(caracteristicas=valor)), esperado)

    def test_caracteristica_inexistente_junto_de_existente_nao_traz_nada(self):
        response = self.buscar(caracteristicas=f"{self.portao.pk},999999")
        self.assertEqual(self.ids(response), [])
        self.assertEqual(response.data["count"], 0)

    def test_filtro_caracteristicas_usa_uma_so_subconsulta_qualquer_que_seja_a_quantidade(self):
        tabela = Imovel.caracteristicas.through._meta.db_table

        def consultas_da_requisicao(ids):
            with CaptureQueriesContext(connection) as consultas:
                response = self.buscar(caracteristicas=",".join(ids))
                self.assertEqual(response.status_code, status.HTTP_200_OK)
            return [c["sql"] for c in consultas.captured_queries]

        def ocorrencias_por_consulta(consultas):
            # a lista pode nem rodar quando o count dá 0 (20 ids sem resultado): compara a forma de
            # cada consulta (quantas vezes ela cita a tabela), não o total da requisição
            return {consulta.count(tabela) for consulta in consultas if tabela in consulta}

        dois = [str(self.portao.pk), str(self.ar.pk)]
        vinte = dois + self._ids_inexistentes(18)
        consultas_dois = consultas_da_requisicao(dois)
        consultas_vinte = consultas_da_requisicao(vinte)
        self.assertTrue(ocorrencias_por_consulta(consultas_dois))
        self.assertEqual(
            ocorrencias_por_consulta(consultas_dois), ocorrencias_por_consulta(consultas_vinte)
        )
        # a tabela M2M aparece uma vez por consulta: a subconsulta agregada, nunca um JOIN por id
        self.assertEqual(ocorrencias_por_consulta(consultas_vinte), {1})
        for sql in consultas_dois + consultas_vinte:
            self.assertNotIn(f'JOIN "{tabela}"', sql)

    def test_paginacao_com_filtro_de_caracteristicas_nao_duplica_linha(self):
        with mock.patch.object(PaginacaoVitrine, "page_size", 1):
            pagina1 = self.buscar(caracteristicas=f"{self.portao.pk}")
            pagina2 = self.buscar(caracteristicas=f"{self.portao.pk}", page=2)
        self.assertEqual(pagina1.data["count"], 2)
        self.assertEqual(len(pagina1.data["results"]), 1)
        self.assertEqual(len(pagina2.data["results"]), 1)
        self.assertNotEqual(self.ids(pagina1), self.ids(pagina2))

    def test_filtro_invalido_da_400_com_mensagem(self):
        response = self.buscar(preco_min="barato")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("preco_min", response.data)
        response = self.buscar(ordenar="aleatorio")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # --- entrada hostil (CR-02, WR-01, tetos do WR-02): sempre 400 {campo: mensagem}, nunca 500 ---

    def test_numero_nao_finito_ou_fora_do_formato_da_400(self):
        campos = ["preco_min", "preco_max", "area_min", "area_max"]
        valores = [
            "NaN", "sNaN", "Infinity", "-Infinity", "inf", "1e400", "1e3", "1_0",
            " 5", "5 ", "٣",  # dígito arábico-índico
        ]
        for campo in campos:
            for valor in valores:
                with self.subTest(campo=campo, valor=valor):
                    response = self.buscar(**{campo: valor})
                    self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                    self.assertEqual(response.data[campo], "Informe um número válido.")

    def test_numero_inteiro_fora_do_formato_da_400(self):
        valores = ["NaN", "1.5", "1_0", "٣", "99999999999999999999"]
        for campo in ["quartos", "suites", "vagas"]:
            for valor in valores:
                with self.subTest(campo=campo, valor=valor):
                    response = self.buscar(**{campo: valor})
                    self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                    self.assertIn(campo, response.data)

    def test_numero_negativo_da_400_com_mensagem(self):
        for campo, valor in [("preco_min", "-5"), ("quartos", "-1")]:
            with self.subTest(campo=campo, valor=valor):
                response = self.buscar(**{campo: valor})
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response.data[campo], "O valor não pode ser negativo.")

    def test_numero_acima_do_teto_da_400_e_no_teto_passa(self):
        self.assertEqual(self.buscar(preco_max="9999999999.99").status_code, status.HTTP_200_OK)
        self.assertEqual(self.buscar(quartos="32767").status_code, status.HTTP_200_OK)
        for campo, valor in [("preco_max", "10000000000"), ("quartos", "32768")]:
            with self.subTest(campo=campo, valor=valor):
                response = self.buscar(**{campo: valor})
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response.data[campo], "O valor passa do máximo permitido.")

    def test_area_tem_o_teto_da_coluna(self):
        # a coluna de área é max_digits=10 (99999999.99); o teto do preço (12 dígitos) não vale para ela
        self.assertEqual(self.buscar(area_max="99999999.99").status_code, status.HTTP_200_OK)
        self.assertEqual(self.buscar(area_min="99999999.99").status_code, status.HTTP_200_OK)
        for campo, valor in [
            ("area_min", "100000000"),
            ("area_max", "100000000"),
            ("area_max", "9999999999.99"),
        ]:
            with self.subTest(campo=campo, valor=valor):
                response = self.buscar(**{campo: valor})
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response.data[campo], "O valor passa do máximo permitido.")

    def test_decimal_com_fracao_continua_aceito(self):
        # a casa tem 150 m²; o terreno, 360 m²
        self.assertEqual(self.ids(self.buscar(area_min="150.5")), [self.terreno.pk])
        self.assertCountEqual(
            self.ids(self.buscar(preco_min="300000")), [self.casa.pk, self.apto.pk]
        )

    def test_byte_nul_em_busca_e_bairro_da_400(self):
        response = self.buscar(busca="a\x00b")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["busca"], "O texto tem um caractere inválido.")
        response = self.buscar(bairro="Moema,a\x00b")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["bairro"], "O texto tem um caractere inválido.")

    def test_byte_nul_em_qualquer_parametro_nunca_da_500(self):
        for campo in [
            "busca", "bairro", "natureza", "finalidade", "ordenar",
            "preco_min", "area_max", "quartos", "caracteristicas",
        ]:
            with self.subTest(campo=campo):
                response = self.buscar(**{campo: "a\x00b"})
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(campo, response.data)

    def test_busca_acima_do_teto_da_400(self):
        self.assertEqual(self.buscar(busca="a" * 200).status_code, status.HTTP_200_OK)
        response = self.buscar(busca="a" * 201)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["busca"], "Use no máximo 200 caracteres.")
        # o teto vale depois do trim, e busca só de espaços continua ignorada
        self.assertEqual(self.buscar(busca=" " + "a" * 200 + " ").status_code, status.HTTP_200_OK)
        self.assertEqual(len(self.ids(self.buscar(busca="   "))), 4)

    def test_bairro_com_itens_demais_ou_longo_demais_da_400(self):
        vinte = ",".join(f"Bairro {n}" for n in range(20))
        self.assertEqual(self.buscar(bairro=vinte).status_code, status.HTTP_200_OK)
        response = self.buscar(bairro=vinte + ",Bairro 20")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["bairro"], "Escolha no máximo 20 bairros.")
        self.assertEqual(self.buscar(bairro="b" * 100).status_code, status.HTTP_200_OK)
        response = self.buscar(bairro="b" * 101)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["bairro"], "Cada bairro tem no máximo 100 caracteres.")

    def test_ordenacao_por_preco(self):
        self.assertEqual(
            self.ids(self.buscar(finalidade="VENDA", ordenar="preco")),
            [self.terreno.pk, self.apto.pk, self.casa.pk],
        )
        self.assertEqual(
            self.ids(self.buscar(finalidade="VENDA", ordenar="-preco")),
            [self.casa.pk, self.apto.pk, self.terreno.pk],
        )

    def test_ordenacao_por_area(self):
        self.assertEqual(
            self.ids(self.buscar(ordenar="area")),
            [self.apto.pk, self.sobrado.pk, self.casa.pk, self.terreno.pk],
        )
        self.assertEqual(
            self.ids(self.buscar(ordenar="-area")),
            [self.terreno.pk, self.casa.pk, self.sobrado.pk, self.apto.pk],
        )

    def test_ordenacao_padrao_e_mais_recentes(self):
        esperado = [self.terreno.pk, self.apto.pk, self.sobrado.pk, self.casa.pk]
        self.assertEqual(self.ids(self.buscar()), esperado)
        self.assertEqual(self.ids(self.buscar(ordenar="recentes")), esperado)

    def test_paginacao(self):
        with mock.patch.object(PaginacaoVitrine, "page_size", 3):
            pagina1 = self.buscar()
            pagina2 = self.buscar(page=2)
        self.assertEqual(pagina1.data["count"], 4)
        self.assertIsNotNone(pagina1.data["next"])
        self.assertEqual(len(pagina1.data["results"]), 3)
        self.assertEqual(len(pagina2.data["results"]), 1)
        self.assertFalse(set(self.ids(pagina1)) & set(self.ids(pagina2)))

    def test_cartao_traz_capa_e_numeros_da_natureza(self):
        cartao = next(i for i in self.buscar().data["results"] if i["id"] == self.casa.pk)
        capa = self.casa.fotos.get(capa=True)
        self.assertTrue(cartao["foto_capa"].endswith(capa.imagem.url))
        self.assertEqual(cartao["bairro"], "Moema")
        self.assertEqual(Decimal(cartao["area"]), Decimal("150.00"))
        self.assertEqual((cartao["quartos"], cartao["suites"], cartao["vagas"]), (3, 1, 2))


class VitrineDetalheTests(VitrineBase):
    """Critério 5: detalhe com fotos, dados da natureza, coordenada e corretor."""

    def detalhe(self, imovel):
        return self.client.get(reverse("publico-imovel", args=[imovel.pk]))

    def test_detalhe_da_casa(self):
        response = self.detalhe(self.casa)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        dados = response.data

        self.assertEqual(len(dados["fotos"]), 2)
        # a capa abre a galeria (regra da API, o app não reordena)
        self.assertEqual([f["capa"] for f in dados["fotos"]], [True, False])

        self.assertEqual(dados["casa"]["quartos"], 3)
        self.assertEqual(Decimal(dados["casa"]["area_livre"]), Decimal("150.00"))
        self.assertIsNone(dados["terreno"])
        self.assertIsNone(dados["apartamento"])

        self.assertEqual(Decimal(dados["endereco"]["latitude"]), Decimal("-23.600000"))
        self.assertEqual(Decimal(dados["endereco"]["longitude"]), Decimal("-46.660000"))
        self.assertEqual(
            dados["endereco"]["cidade"], {"id": self.sp.pk, "nome": "São Paulo", "uf": "SP"}
        )

        self.assertEqual(dados["corretor_responsavel"]["nome"], self.corretor.nome)
        self.assertEqual(dados["corretor_responsavel"]["whatsapp"], self.corretor.whatsapp)
        self.assertCountEqual(
            [c["nome"] for c in dados["caracteristicas"]], ["Portão eletrônico", "Ar-condicionado"]
        )

    def test_detalhe_do_apartamento_usa_predio(self):
        dados = self.detalhe(self.apto).data
        self.assertEqual(dados["apartamento"]["quartos"], 2)
        self.assertEqual(dados["apartamento"]["predio"]["nome"], "Edifício Aurora")
        self.assertEqual(dados["endereco"]["bairro"], "Bela Vista")
        self.assertEqual(Decimal(dados["endereco"]["latitude"]), Decimal("-23.56"))

    def test_detalhe_do_terreno(self):
        dados = self.detalhe(self.terreno).data
        self.assertEqual(Decimal(dados["terreno"]["area_total"]), Decimal("360.00"))
        self.assertEqual(dados["terreno"]["topografia"], "PLANO")

    def test_rascunho_e_empresa_inativa_dao_404(self):
        self.assertEqual(self.detalhe(self.rascunho).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.detalhe(self.da_inativa).status_code, status.HTTP_404_NOT_FOUND)

    def test_corretor_desativado_some_mas_imovel_continua(self):
        self.corretor.is_active = False
        self.corretor.save(update_fields=["is_active"])
        response = self.detalhe(self.casa)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["corretor_responsavel"])


def todas_as_chaves(dados):
    """Junta as chaves de todos os níveis da resposta (dicts dentro de listas etc.)."""
    if isinstance(dados, dict):
        chaves = set(dados)
        for valor in dados.values():
            chaves |= todas_as_chaves(valor)
        return chaves
    if isinstance(dados, list):
        chaves = set()
        for item in dados:
            chaves |= todas_as_chaves(item)
        return chaves
    return set()


class VitrineSemTokenESemVazamentoTests(VitrineBase):
    """Critério 6: não exige token e não devolve proprietário, documentos nem dado interno."""

    CHAVES_PROIBIDAS = {
        "proprietario", "documento", "nome_razao_social", "codigo", "empresa", "publicado",
        "pode_publicar", "atualizado_em", "criado_em", "complemento", "valor_condominio_base",
        "unidades_por_andar", "imovel",
        "vendido", "situacao", "documentos", "historico",
    }

    VALORES_PROIBIDOS = [
        "Dona Secreta", "17662633055", "176.626.330-55", "CASA-INTERNO", "APTO-INTERNO",
        "TERRENO-INTERNO", "LOTE-INTERNO", "Casa 2", "800.00",
    ]

    def setUp(self):
        super().setUp()
        self.imovel_lote, _ = publicar_lote_da_vitrine(self)

    def respostas(self):
        yield self.client.get(reverse("publico-cidades"))
        yield self.buscar()
        yield self.client.get(reverse("publico-vitrine-corretor", args=[self.corretor.pk]))
        yield self.client.get(reverse("publico-vitrine-empresa", args=[self.empresa.pk]))
        for imovel in (self.casa, self.apto, self.terreno, self.imovel_lote):
            yield self.client.get(reverse("publico-imovel", args=[imovel.pk]))

    def test_nao_exige_token_nem_se_atrapalha_com_token_invalido(self):
        for response in self.respostas():
            self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.client.credentials(HTTP_AUTHORIZATION="Token token-vencido-ou-invalido")
        for response in self.respostas():
            self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_nenhuma_chave_interna_aparece(self):
        for response in self.respostas():
            vazadas = todas_as_chaves(response.json()) & self.CHAVES_PROIBIDAS
            self.assertFalse(vazadas, f"{response.request['PATH_INFO']} devolveu {vazadas}")

    def test_nenhum_valor_interno_aparece(self):
        for response in self.respostas():
            corpo = response.content.decode()
            for segredo in self.VALORES_PROIBIDOS:
                self.assertNotIn(segredo, corpo, f"{response.request['PATH_INFO']} vazou {segredo}")

    def test_api_publica_so_aceita_leitura(self):
        url = reverse("publico-imovel", args=[self.casa.pk])
        self.assertEqual(
            self.client.patch(url, {"titulo": "x"}).status_code, status.HTTP_405_METHOD_NOT_ALLOWED
        )
        self.assertEqual(self.client.delete(url).status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class VitrineDoAnuncianteTests(VitrineBase):
    """07.1 (D-01, D-10): imóveis na vitrine de um corretor e de uma empresa, pela API pública.

    Mesmo recorte, serializer, paginação e limite de taxa da vitrine da cidade; só `page` é lido.
    """

    def setUp(self):
        super().setUp()
        self.outro_corretor = criar_corretor(self.empresa, email="outro@teste.com")
        self.do_outro = criar_imovel(
            self.empresa, self.outro_corretor, self.proprietario, codigo="OUTRO-1",
            titulo="Casa do outro corretor", endereco=criar_endereco(self.sp, "Itaim"),
            publicado=True,
        )

    def url_corretor(self, corretor=None):
        return reverse("publico-vitrine-corretor", args=[(corretor or self.corretor).pk])

    def url_empresa(self, empresa=None):
        return reverse("publico-vitrine-empresa", args=[(empresa or self.empresa).pk])

    def ids_de(self, url, **params):
        return self.ids(self.client.get(url, params))

    def mais_recentes_primeiro(self, imoveis):
        return [i.pk for i in sorted(imoveis, key=lambda i: (i.criado_em, i.pk), reverse=True)]

    # --- recorte ---

    def test_corretor_lista_so_os_imoveis_dele_na_vitrine_mais_recentes_primeiro(self):
        esperado = self.mais_recentes_primeiro(
            [self.casa, self.sobrado, self.apto, self.terreno, self.em_campinas]
        )
        self.assertEqual(self.ids_de(self.url_corretor()), esperado)

    def test_corretor_nao_lista_rascunho_nem_imovel_de_outro_corretor_nem_de_empresa_inativa(self):
        ids = self.ids_de(self.url_corretor())
        self.assertNotIn(self.rascunho.pk, ids)
        self.assertNotIn(self.do_outro.pk, ids)
        self.assertNotIn(self.da_inativa.pk, ids)
        self.assertEqual(self.ids_de(self.url_corretor(self.outro_corretor)), [self.do_outro.pk])

    def test_empresa_lista_os_imoveis_de_todos_os_corretores_dela(self):
        esperado = self.mais_recentes_primeiro(
            [self.casa, self.sobrado, self.apto, self.terreno, self.em_campinas, self.do_outro]
        )
        ids = self.ids_de(self.url_empresa())
        self.assertEqual(ids, esperado)
        self.assertNotIn(self.da_inativa.pk, ids)
        self.assertNotIn(self.rascunho.pk, ids)

    def test_imovel_vendido_ou_reservado_sai_da_vitrine_do_anunciante(self):
        self.casa.situacao = Imovel.Situacao.VENDIDO
        self.casa.save(update_fields=["situacao"])
        self.sobrado.situacao = Imovel.Situacao.RESERVADO
        self.sobrado.save(update_fields=["situacao"])
        for url in (self.url_corretor(), self.url_empresa()):
            ids = self.ids_de(url)
            self.assertNotIn(self.casa.pk, ids)
            self.assertNotIn(self.sobrado.pk, ids)
            self.assertIn(self.apto.pk, ids)

    def test_item_tem_exatamente_as_chaves_do_cartao_da_vitrine(self):
        for url in (self.url_corretor(), self.url_empresa()):
            for item in self.client.get(url).json()["results"]:
                self.assertEqual(set(item), VitrineAllowlistExataTests.CHAVES_ITEM)
                self.assertNotIn("cidade", item)

    # --- paginação ---

    def test_paginacao_com_next_absoluto_na_mesma_rota(self):
        with mock.patch.object(PaginacaoVitrine, "page_size", 2):
            pagina1 = self.client.get(self.url_corretor())
            pagina3 = self.client.get(self.url_corretor(), {"page": 3})
            pagina4 = self.client.get(self.url_corretor(), {"page": 4})
        self.assertEqual(pagina1.data["count"], 5)
        self.assertEqual(
            pagina1.data["next"], f"http://testserver{self.url_corretor()}?page=2"
        )
        self.assertEqual(len(pagina1.data["results"]), 2)
        self.assertEqual(len(pagina3.data["results"]), 1)
        self.assertIsNone(pagina3.data["next"])
        self.assertEqual(pagina4.status_code, status.HTTP_404_NOT_FOUND)

    def test_paginacao_da_empresa_aponta_para_a_rota_da_empresa(self):
        with mock.patch.object(PaginacaoVitrine, "page_size", 2):
            pagina1 = self.client.get(self.url_empresa())
        self.assertEqual(pagina1.data["next"], f"http://testserver{self.url_empresa()}?page=2")

    def test_so_page_e_lido_busca_ordenar_e_natureza_sao_ignorados(self):
        for url in (self.url_corretor(), self.url_empresa()):
            sem_parametros = self.ids_de(url)
            com_parametros = self.ids_de(
                url, busca="inexistente-xyz", ordenar="preco", natureza="TERRENO",
            )
            self.assertEqual(com_parametros, sem_parametros)

    # --- 404 uniforme ---

    def test_anunciante_inexistente_inativo_ou_que_nao_e_corretor_da_404_com_detail(self):
        gestor = Usuario.objects.create_user(
            email="gestor2@teste.com", senha="senha-forte-123", nome="Gestor",
            perfil=Usuario.Perfil.GESTOR, empresa=self.empresa,
        )
        corretor_da_inativa = Usuario.objects.get(email="c2@teste.com")
        desativado = criar_corretor(self.empresa, email="desativado@teste.com")
        desativado.is_active = False
        desativado.save(update_fields=["is_active"])
        empresa_inativa = corretor_da_inativa.empresa
        casos = {
            "corretor inexistente": reverse("publico-vitrine-corretor", args=[999999]),
            "corretor desativado": self.url_corretor(desativado),
            "usuario gestor": self.url_corretor(gestor),
            "corretor de empresa inativa": self.url_corretor(corretor_da_inativa),
            "empresa inexistente": reverse("publico-vitrine-empresa", args=[999999]),
            "empresa inativa": self.url_empresa(empresa_inativa),
        }
        # a mensagem do get_object_or_404 cita o modelo da rota; dentro de cada rota é a mesma
        mensagens = {"corretor": set(), "empresa": set()}
        for nome, url in casos.items():
            with self.subTest(caso=nome):
                response = self.client.get(url)
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
                self.assertIn("detail", response.data)
                mensagens[nome.split()[0] if nome.startswith("empresa") else "corretor"].add(
                    str(response.data["detail"])
                )
        self.assertEqual(len(mensagens["corretor"]), 1, mensagens)
        self.assertEqual(len(mensagens["empresa"]), 1, mensagens)

    def test_anunciante_ativo_sem_imoveis_da_200_com_lista_vazia(self):
        sem_imoveis = criar_corretor(self.empresa, email="vazio@teste.com")
        response = self.client.get(self.url_corretor(sem_imoveis))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["results"], [])

        vazia = criar_empresa(nome="Vazia", cnpj="45.723.174/0001-10")
        response = self.client.get(self.url_empresa(vazia))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["results"], [])

    def test_desativar_o_corretor_tira_a_vitrine_dele_mas_a_da_empresa_continua(self):
        self.corretor.is_active = False
        self.corretor.save(update_fields=["is_active"])
        self.assertEqual(
            self.client.get(self.url_corretor()).status_code, status.HTTP_404_NOT_FOUND
        )
        self.assertIn(self.casa.pk, self.ids_de(self.url_empresa()))

    # --- limite de taxa, método, token, consultas ---

    def test_limite_de_taxa_vale_para_as_duas_rotas(self):
        for url in (self.url_corretor(), self.url_empresa()):
            cache.clear()
            with mock.patch.object(ThrottleVitrinePublica, "rate", "2/min", create=True):
                for _ in range(2):
                    self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)
                response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS, url)
            self.assertIn("Retry-After", response)

    def test_so_aceita_leitura(self):
        for url in (self.url_corretor(), self.url_empresa()):
            self.assertEqual(
                self.client.post(url, {"titulo": "x"}).status_code,
                status.HTTP_405_METHOD_NOT_ALLOWED,
            )

    def test_token_invalido_nao_derruba_as_rotas(self):
        self.client.credentials(HTTP_AUTHORIZATION="Token token-vencido-ou-invalido")
        for url in (self.url_corretor(), self.url_empresa()):
            self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)

    def test_numero_de_consultas_nao_cresce_com_o_numero_de_itens(self):
        for url in (self.url_corretor(), self.url_empresa()):
            contagens = []
            for tamanho in (2, 4):
                with mock.patch.object(PaginacaoVitrine, "page_size", tamanho):
                    with CaptureQueriesContext(connection) as consultas:
                        response = self.client.get(url)
                self.assertEqual(len(response.data["results"]), tamanho)
                contagens.append(len(consultas))
            self.assertEqual(contagens[0], contagens[1], url)


class VitrineSituacaoTests(VitrineBase):
    """D-04: reservado, vendido e alugado saem da vitrine — lista, detalhe e cidades atendidas."""

    def setUp(self):
        super().setUp()
        self.gestor = criar_gestor(self.empresa)

    def _mudar(self, imovel, situacao):
        imovel.mudar_situacao(situacao, self.gestor)

    def test_reservado_vendido_e_alugado_somem_da_lista_da_cidade(self):
        self._mudar(self.casa, Imovel.Situacao.RESERVADO)
        self._mudar(self.sobrado, Imovel.Situacao.VENDIDO)
        self._mudar(self.terreno, Imovel.Situacao.ALUGADO)

        ids = self.ids(self.buscar())

        self.assertCountEqual(ids, [self.apto.pk])
        for fora in (self.casa, self.sobrado, self.terreno):
            self.assertNotIn(fora.pk, ids)

    def test_detalhe_fora_da_vitrine_responde_o_mesmo_404_de_id_inexistente(self):
        inexistente = self.client.get(reverse("publico-imovel", args=[999999]))
        self.assertEqual(inexistente.status_code, status.HTTP_404_NOT_FOUND)

        casos = [
            (self.casa, Imovel.Situacao.RESERVADO),
            (self.sobrado, Imovel.Situacao.VENDIDO),
            (self.terreno, Imovel.Situacao.ALUGADO),
        ]
        for imovel, situacao in casos:
            with self.subTest(situacao=situacao):
                self._mudar(imovel, situacao)
                response = self.client.get(reverse("publico-imovel", args=[imovel.pk]))
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
                self.assertEqual(response.json(), inexistente.json())

    def test_cidade_sem_imovel_na_vitrine_some_das_cidades_atendidas(self):
        cidades = lambda: [c["id"] for c in self.client.get(reverse("publico-cidades")).data]  # noqa: E731
        self.assertIn(self.campinas.pk, cidades())

        self._mudar(self.em_campinas, Imovel.Situacao.RESERVADO)
        self.assertNotIn(self.campinas.pk, cidades())
        self.assertIn(self.sp.pk, cidades())

        self._mudar(self.em_campinas, Imovel.Situacao.DISPONIVEL)
        self.assertIn(self.campinas.pk, cidades())

    def test_mudanca_entre_a_lista_e_o_detalhe_vale_na_leitura_seguinte(self):
        """API-07: a regra é avaliada a cada requisição, sem cache nem estado parcial."""
        url = reverse("publico-imovel", args=[self.casa.pk])
        self.assertIn(self.casa.pk, self.ids(self.buscar()))
        self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)

        self._mudar(self.casa, Imovel.Situacao.VENDIDO)  # alguém muda a situação no meio

        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)
        self.assertNotIn(self.casa.pk, self.ids(self.buscar()))


def criar_loteamento(empresa, nome="Jardim das Flores", **extra):
    cidade = Cidade.objects.get_or_create(nome="São Paulo", uf="SP")[0]
    dados = dict(
        empresa=empresa, nome=nome, cidade=cidade, incorporadora="Incorporadora Alfa",
        asfalto=True, agua=True, energia=True, esgoto=False, iluminacao=True,
        areas_de_lazer="Praça e quadra poliesportiva",
    )
    dados.update(extra)
    return Loteamento.objects.create(**dados)


def criar_imovel_lote(empresa, corretor, proprietario, codigo, **extra):
    imovel = criar_imovel(
        empresa, corretor, proprietario, codigo=codigo, natureza=Imovel.Natureza.LOTE,
        preco_venda="120000.00", **extra,
    )
    imovel.refresh_from_db()  # preço volta como Decimal, como chega pelas telas e pela API
    return imovel


def dados_lote(quadra, **extra):
    dados = {
        "quadra": quadra.pk, "numero": "7", "area": "250.00", "frente": "10.00",
        "fundo": "25.00", "entrada_minima": "12000.00", "parcelas_maximas": 120,
        "condicoes_parcelamento": "Correção anual pelo IPCA",
    }
    dados.update(extra)
    return dados


def criar_lote(imovel, quadra, **extra):
    dados = dados_lote(quadra, **extra)
    dados["quadra"] = quadra
    return Lote.objects.create(imovel=imovel, **dados)


class LoteamentoModelTests(TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.loteamento = criar_loteamento(self.empresa)
        self.quadra_a = Quadra.objects.create(loteamento=self.loteamento, identificacao="A")
        self.imovel1 = criar_imovel_lote(self.empresa, self.corretor, self.proprietario, "L1")
        self.imovel2 = criar_imovel_lote(self.empresa, self.corretor, self.proprietario, "L2")

    def _lote(self, imovel, **extra):
        dados = dados_lote(self.quadra_a, **extra)
        dados["quadra"] = self.quadra_a
        return Lote(imovel=imovel, **dados)

    def test_cadastro_do_lote_na_quadra(self):
        lote = self._lote(self.imovel1)
        lote.full_clean()
        lote.save()
        self.assertEqual(self.quadra_a.lotes.count(), 1)

    def test_quadra_repetida_no_loteamento_e_recusada(self):
        repetida = Quadra(loteamento=self.loteamento, identificacao="A")
        with self.assertRaises(ValidationError):
            repetida.full_clean()

    def test_mesma_quadra_em_outro_loteamento_e_aceita(self):
        outro = criar_loteamento(self.empresa, nome="Recanto Verde")
        Quadra(loteamento=outro, identificacao="A").full_clean()

    def test_numero_de_lote_repetido_na_quadra_e_recusado(self):
        criar_lote(self.imovel1, self.quadra_a)
        with self.assertRaises(ValidationError) as contexto:
            self._lote(self.imovel2).full_clean()
        self.assertIn("Já existe um lote com esse número nesta quadra.", contexto.exception.messages)

    def test_banco_tambem_recusa_numero_repetido(self):
        criar_lote(self.imovel1, self.quadra_a)
        with self.assertRaises(IntegrityError):
            criar_lote(self.imovel2, self.quadra_a)

    def test_mesmo_numero_em_outra_quadra_e_aceito(self):
        criar_lote(self.imovel1, self.quadra_a)
        quadra_b = Quadra.objects.create(loteamento=self.loteamento, identificacao="B")
        lote = self._lote(self.imovel2)
        lote.quadra = quadra_b
        lote.full_clean()

    def test_quadra_de_outra_empresa_e_recusada(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        quadra_de_fora = Quadra.objects.create(
            loteamento=criar_loteamento(outra_empresa), identificacao="A"
        )
        lote = self._lote(self.imovel1)
        lote.quadra = quadra_de_fora
        with self.assertRaises(ValidationError) as contexto:
            lote.full_clean()
        self.assertIn("quadra", contexto.exception.message_dict)

    def test_entrada_maior_que_preco_e_recusada(self):
        with self.assertRaises(ValidationError) as contexto:
            self._lote(self.imovel1, entrada_minima="200000.00").full_clean()
        self.assertIn("entrada_minima", contexto.exception.message_dict)

    def test_lote_nao_entra_em_casa(self):
        casa = criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="CASA")
        with self.assertRaises(ValidationError):
            self._lote(casa).full_clean()

    def test_resumo_por_quadra_conta_disponiveis_e_vendidos(self):
        criar_lote(self.imovel1, self.quadra_a, numero="1")
        criar_lote(self.imovel2, self.quadra_a, numero="2")
        self.assertEqual(
            self.quadra_a.resumo_lotes(), {"total": 2, "disponiveis": 2, "vendidos": 0}
        )

    def test_lote_publica_como_imovel(self):
        endereco = Endereco.objects.create(
            cep="04000-000", logradouro="Rua 1", numero="s/n", bairro="Jardim das Flores",
            cidade=self.loteamento.cidade, latitude="-23.5", longitude="-46.6",
        )
        self.imovel1.endereco = endereco
        self.imovel1.save()
        criar_lote(self.imovel1, self.quadra_a)
        self.imovel1.publicar()
        self.assertTrue(Imovel.objects.get(pk=self.imovel1.pk).publicado)


class PainelLoteamentoTests(TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.loteamento = criar_loteamento(self.empresa)
        self.quadra_a = Quadra.objects.create(loteamento=self.loteamento, identificacao="A")
        self.imovel = criar_imovel_lote(self.empresa, self.corretor, self.proprietario, "L1")
        self.client.force_login(self.corretor)

    def test_gestor_nao_acessa_loteamentos(self):
        self.client.force_login(criar_gestor(self.empresa))
        response = self.client.get(reverse("imoveis:loteamento_list"))
        self.assertEqual(response.status_code, 403)

    def test_cadastra_loteamento(self):
        response = self.client.post(reverse("imoveis:loteamento_create"), {
            "nome": "Recanto Verde", "cidade": self.loteamento.cidade.pk,
            "incorporadora": "Beta", "asfalto": "on", "agua": "on", "areas_de_lazer": "Praça",
        })
        loteamento = Loteamento.objects.get(nome="Recanto Verde")
        self.assertRedirects(response, reverse("imoveis:loteamento_detail", args=[loteamento.pk]))
        self.assertEqual(loteamento.empresa, self.empresa)
        self.assertTrue(loteamento.asfalto)
        self.assertFalse(loteamento.esgoto)

    def test_nome_repetido_mostra_erro(self):
        response = self.client.post(reverse("imoveis:loteamento_create"), {
            "nome": "Jardim das Flores", "cidade": self.loteamento.cidade.pk, "incorporadora": "X",
        })
        self.assertContains(response, "Já existe um loteamento com esse nome")

    def test_cadastra_quadra_na_tela_do_loteamento(self):
        url = reverse("imoveis:loteamento_detail", args=[self.loteamento.pk])
        response = self.client.post(url, {"identificacao": "B"})
        self.assertRedirects(response, url)
        self.assertTrue(self.loteamento.quadras.filter(identificacao="B").exists())

    def test_quadra_repetida_mostra_erro(self):
        response = self.client.post(
            reverse("imoveis:loteamento_detail", args=[self.loteamento.pk]), {"identificacao": "A"}
        )
        self.assertContains(response, "Já existe uma quadra com essa identificação")

    def test_tela_mostra_disponiveis_e_vendidos_por_quadra(self):
        criar_lote(self.imovel, self.quadra_a)
        Quadra.objects.create(loteamento=self.loteamento, identificacao="B")
        response = self.client.get(reverse("imoveis:loteamento_detail", args=[self.loteamento.pk]))
        resumos = {q.identificacao: r for q, r in response.context["quadras"]}
        self.assertEqual(resumos["A"], {"total": 1, "disponiveis": 1, "vendidos": 0})
        self.assertEqual(resumos["B"], {"total": 0, "disponiveis": 0, "vendidos": 0})
        self.assertContains(response, "Vendidos")

    def test_loteamento_de_outra_empresa_da_404(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        de_fora = criar_loteamento(outra_empresa)
        response = self.client.get(reverse("imoveis:loteamento_detail", args=[de_fora.pk]))
        self.assertEqual(response.status_code, 404)

    def test_atalho_novo_lote_ja_escolhe_a_natureza(self):
        response = self.client.get(reverse("imoveis:imovel_create") + "?natureza=LOTE")
        self.assertEqual(response.context["form"].initial["natureza"], Imovel.Natureza.LOTE)

    def test_tela_do_lote_so_lista_quadras_da_empresa(self):
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        Quadra.objects.create(loteamento=criar_loteamento(outra_empresa), identificacao="Z")
        response = self.client.get(reverse("imoveis:imovel_detail", args=[self.imovel.pk]))
        form = response.context["detalhes_form"]
        self.assertEqual(list(form.fields["quadra"].queryset), [self.quadra_a])

    def test_salva_lote_e_detalhe_mostra_quadra_e_loteamento(self):
        response = self.client.post(
            reverse("imoveis:imovel_atualizar_detalhes", args=[self.imovel.pk]),
            dados_lote(self.quadra_a),
        )
        self.assertRedirects(response, reverse("imoveis:imovel_detail", args=[self.imovel.pk]))
        response = self.client.get(reverse("imoveis:imovel_detail", args=[self.imovel.pk]))
        self.assertContains(response, "Quadra A")
        self.assertContains(response, "Jardim das Flores")
        self.assertContains(response, "Asfalto")
        self.assertContains(response, "até 120x")

    def test_lote_repetido_mostra_mensagem_clara(self):
        outro = criar_imovel_lote(self.empresa, self.corretor, self.proprietario, "L2")
        criar_lote(outro, self.quadra_a)
        response = self.client.post(
            reverse("imoveis:imovel_atualizar_detalhes", args=[self.imovel.pk]),
            dados_lote(self.quadra_a),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Já existe um lote com esse número nesta quadra.")


class ApiLoteamentoTests(APITestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.loteamento = criar_loteamento(self.empresa)
        self.quadra_a = Quadra.objects.create(loteamento=self.loteamento, identificacao="A")
        self.imovel = criar_imovel_lote(self.empresa, self.corretor, self.proprietario, "L1")
        self.outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        self.loteamento_de_fora = criar_loteamento(self.outra_empresa, nome="De fora")
        self.quadra_de_fora = Quadra.objects.create(
            loteamento=self.loteamento_de_fora, identificacao="A"
        )
        self.client.force_authenticate(self.corretor)

    def test_cria_loteamento(self):
        response = self.client.post(reverse("loteamento-list"), {
            "nome": "Recanto Verde", "cidade": self.loteamento.cidade.pk,
            "incorporadora": "Beta", "agua": True,
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Loteamento.objects.get(pk=response.data["id"]).empresa, self.empresa)

    def test_lista_so_loteamentos_da_empresa(self):
        response = self.client.get(reverse("loteamento-list"))
        self.assertEqual([l["nome"] for l in response.data], ["Jardim das Flores"])

    def test_loteamento_de_outra_empresa_da_404(self):
        response = self.client.get(reverse("loteamento-detail", args=[self.loteamento_de_fora.pk]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_loteamento_traz_resumo_por_quadra(self):
        criar_lote(self.imovel, self.quadra_a)
        response = self.client.get(reverse("loteamento-detail", args=[self.loteamento.pk]))
        self.assertEqual(response.data["quadras"], [
            {"id": self.quadra_a.pk, "identificacao": "A", "total": 1, "disponiveis": 1, "vendidos": 0}
        ])

    def test_cria_quadra(self):
        response = self.client.post(reverse("quadra-list"), {
            "loteamento": self.loteamento.pk, "identificacao": "B",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_quadra_repetida_e_recusada(self):
        response = self.client.post(reverse("quadra-list"), {
            "loteamento": self.loteamento.pk, "identificacao": "A",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_nao_cria_quadra_em_loteamento_de_outra_empresa(self):
        response = self.client.post(reverse("quadra-list"), {
            "loteamento": self.loteamento_de_fora.pk, "identificacao": "B",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("loteamento", response.data)

    def test_quadra_de_outra_empresa_da_404(self):
        response = self.client.get(reverse("quadra-detail", args=[self.quadra_de_fora.pk]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_put_detalhes_do_lote(self):
        response = self.client.put(
            reverse("imovel-detalhes", args=[self.imovel.pk]), dados_lote(self.quadra_a),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["quadra"]["loteamento"]["nome"], "Jardim das Flores")

    def test_detalhe_do_lote_traz_quadra_e_loteamento(self):
        criar_lote(self.imovel, self.quadra_a)
        response = self.client.get(reverse("imovel-detail", args=[self.imovel.pk]))
        lote = response.data["lote"]
        self.assertEqual(response.data["natureza"], "LOTE")
        self.assertEqual(response.data["preco_venda"], "120000.00")
        self.assertEqual(lote["numero"], "7")
        self.assertEqual(lote["parcelas_maximas"], 120)
        self.assertFalse(lote["vendido"])
        self.assertEqual(lote["quadra"]["identificacao"], "A")
        self.assertTrue(lote["quadra"]["loteamento"]["asfalto"])
        self.assertEqual(lote["quadra"]["loteamento"]["incorporadora"], "Incorporadora Alfa")

    def test_lote_repetido_e_recusado_com_mensagem(self):
        outro = criar_imovel_lote(self.empresa, self.corretor, self.proprietario, "L2")
        criar_lote(outro, self.quadra_a)
        response = self.client.put(
            reverse("imovel-detalhes", args=[self.imovel.pk]), dados_lote(self.quadra_a),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            "Já existe um lote com esse número nesta quadra.",
            [str(m) for m in response.data["non_field_errors"]],
        )

    def test_nao_liga_lote_a_quadra_de_outra_empresa(self):
        response = self.client.put(
            reverse("imovel-detalhes", args=[self.imovel.pk]), dados_lote(self.quadra_de_fora),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("quadra", response.data)

    def test_lote_publicado_pela_api(self):
        criar_lote(self.imovel, self.quadra_a)
        self.client.put(reverse("imovel-endereco", args=[self.imovel.pk]), {
            "cep": "04000-000", "logradouro": "Rua 1", "numero": "s/n", "bairro": "Jardim das Flores",
            "cidade": self.loteamento.cidade.pk, "latitude": "-23.5", "longitude": "-46.6",
        })
        self.client.force_authenticate(criar_gestor(self.empresa))
        response = self.client.post(reverse("gestao-imovel-publicar", args=[self.imovel.pk]))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertTrue(response.data["publicado"])
        self.assertEqual(response.data["lote"]["quadra"]["loteamento"]["nome"], "Jardim das Flores")

    def test_nao_exclui_quadra_com_lotes(self):
        criar_lote(self.imovel, self.quadra_a)
        response = self.client.delete(reverse("quadra-detail", args=[self.quadra_a.pk]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# --- Lote na vitrine pública (APP05, D-06) -----------------------------------


def criar_lote_da_vitrine(imovel, empresa, cidade, **extra):
    """Lote com quadra e loteamento. `cidade` é a do LOTEAMENTO, de propósito diferente da
    do endereço do imóvel nos testes: a vitrine só lê o endereço do imóvel.
    """
    loteamento, _ = Loteamento.objects.get_or_create(
        empresa=empresa, nome="Jardim das Flores",
        defaults=dict(
            cidade=cidade, incorporadora="Incorporadora Alfa", asfalto=True, agua=True,
            energia=True, esgoto=False, iluminacao=True,
            areas_de_lazer="Praça e quadra poliesportiva",
        ),
    )
    quadra, _ = Quadra.objects.get_or_create(loteamento=loteamento, identificacao="B")
    dados = dict(
        numero="7", area="360.00", frente="12.00", fundo="30.00", entrada_minima="12000.00",
        parcelas_maximas=120, condicoes_parcelamento="Correção anual pelo IPCA",
    )
    dados.update(extra)
    return Lote.objects.create(imovel=imovel, quadra=quadra, **dados)


def publicar_lote_da_vitrine(teste):
    """Publica (Imovel.publicar) um lote de endereço em São Paulo e loteamento em Campinas."""
    imovel = criar_imovel(
        teste.empresa, teste.corretor, teste.proprietario, codigo="LOTE-INTERNO-1",
        titulo="Lote no Jardim das Flores", natureza=Imovel.Natureza.LOTE,
        preco_venda="120000.00",
        endereco=criar_endereco(teste.sp, "Vila Nova", cep="01310-100"),
    )
    lote = criar_lote_da_vitrine(imovel, teste.empresa, teste.campinas)
    imovel.publicar()
    return imovel, lote


class VitrineLoteTests(VitrineBase):
    """D-06: o lote é natureza pública — na lista (área do lote) e no detalhe (bloco lote)."""

    def setUp(self):
        super().setUp()
        # o endereço do imóvel é em São Paulo; o loteamento fica em Campinas de propósito
        self.imovel_lote, self.lote = publicar_lote_da_vitrine(self)

    def detalhe(self, imovel):
        return self.client.get(reverse("publico-imovel", args=[imovel.pk]))

    def test_lote_aparece_na_cidade_do_endereco_do_imovel_e_nao_na_do_loteamento(self):
        self.assertIn(self.imovel_lote.pk, self.ids(self.buscar(natureza="LOTE")))
        self.assertEqual(self.ids(self.buscar(self.campinas, natureza="LOTE")), [])

    def test_item_da_lista_traz_a_area_do_lote_e_quartos_suites_vagas_nulos(self):
        item = self.buscar(natureza="LOTE").data["results"][0]
        self.assertEqual(item["id"], self.imovel_lote.pk)
        self.assertEqual(item["natureza"], "LOTE")
        self.assertEqual(Decimal(item["area"]), Decimal("360.00"))
        self.assertEqual(item["bairro"], "Vila Nova")
        self.assertIsNone(item["quartos"])
        self.assertIsNone(item["suites"])
        self.assertIsNone(item["vagas"])

    def test_filtro_e_ordenacao_por_area_incluem_o_lote(self):
        self.assertIn(self.imovel_lote.pk, self.ids(self.buscar(area_min="300", area_max="400")))
        self.assertNotIn(self.imovel_lote.pk, self.ids(self.buscar(area_min="400")))
        # casa 150 m², apartamento 70 m², terreno 360 m², lote 360 m²
        ordenados = self.ids(self.buscar(ordenar="-area"))
        self.assertCountEqual(ordenados[:2], [self.terreno.pk, self.imovel_lote.pk])
        crescente = self.ids(self.buscar(ordenar="area"))
        self.assertEqual(crescente[:3], [self.apto.pk, self.sobrado.pk, self.casa.pk])
        self.assertCountEqual(crescente[-2:], [self.terreno.pk, self.imovel_lote.pk])

    def test_detalhe_do_lote_traz_o_bloco_lote_e_as_outras_naturezas_nulas(self):
        dados = self.detalhe(self.imovel_lote).data
        self.assertEqual(dados["natureza"], "LOTE")
        self.assertIsNone(dados["casa"])
        self.assertIsNone(dados["terreno"])
        self.assertIsNone(dados["apartamento"])

        lote = dados["lote"]
        self.assertEqual(lote["numero"], "7")
        self.assertEqual(Decimal(lote["area"]), Decimal("360.00"))
        self.assertEqual(Decimal(lote["frente"]), Decimal("12.00"))
        self.assertEqual(Decimal(lote["fundo"]), Decimal("30.00"))
        self.assertEqual(Decimal(lote["entrada_minima"]), Decimal("12000.00"))
        self.assertEqual(lote["parcelas_maximas"], 120)
        self.assertEqual(lote["condicoes_parcelamento"], "Correção anual pelo IPCA")
        self.assertEqual(lote["quadra"]["identificacao"], "B")
        loteamento = lote["quadra"]["loteamento"]
        self.assertEqual(loteamento["nome"], "Jardim das Flores")
        self.assertEqual(loteamento["incorporadora"], "Incorporadora Alfa")
        self.assertEqual(
            (loteamento["asfalto"], loteamento["agua"], loteamento["energia"],
             loteamento["esgoto"], loteamento["iluminacao"]),
            (True, True, True, False, True),
        )
        self.assertEqual(loteamento["areas_de_lazer"], "Praça e quadra poliesportiva")
        # o preço do lote é o preço do imóvel — sem duplicar dentro do bloco
        self.assertEqual(Decimal(dados["preco_venda"]), Decimal("120000.00"))
        self.assertNotIn("preco", lote)
        # endereço do detalhe é o do imóvel (São Paulo), não a cidade do loteamento
        self.assertEqual(dados["endereco"]["cidade"]["nome"], "São Paulo")

    def test_detalhe_da_casa_traz_lote_nulo(self):
        self.assertIsNone(self.detalhe(self.casa).data["lote"])

    def test_detalhe_do_lote_tem_numero_fixo_de_consultas(self):
        with self.assertNumQueries(3):  # imóvel (todos os joins, lote incluso), fotos, características
            self.assertEqual(self.detalhe(self.imovel_lote).status_code, status.HTTP_200_OK)

    def test_lote_reservado_some_da_lista_e_o_detalhe_da_404(self):
        self.imovel_lote.mudar_situacao(Imovel.Situacao.RESERVADO, criar_gestor(self.empresa))
        self.assertNotIn(self.imovel_lote.pk, self.ids(self.buscar(natureza="LOTE")))
        self.assertEqual(self.detalhe(self.imovel_lote).status_code, status.HTTP_404_NOT_FOUND)


class VitrineAllowlistExataTests(VitrineBase):
    """D-10: o conjunto de chaves de todo objeto público é travado por igualdade exata.

    Complementa o deny-list de VitrineSemTokenESemVazamentoTests: campo novo num serializer
    público só aparece se alguém mudar estes conjuntos de propósito.
    """

    CHAVES_ITEM = {
        "id", "titulo", "natureza", "finalidade", "preco_venda", "preco_aluguel",
        "preco_condominio", "bairro", "area", "quartos", "suites", "vagas", "foto_capa",
    }
    CHAVES_DETALHE = {
        "id", "titulo", "natureza", "finalidade", "preco_venda", "preco_aluguel",
        "preco_condominio", "preco_iptu", "descricao", "caracteristicas", "casa", "terreno",
        "apartamento", "lote", "endereco", "fotos", "corretor_responsavel",
    }
    CHAVES_ENDERECO = {"logradouro", "numero", "bairro", "cep", "cidade", "latitude", "longitude"}
    CHAVES_CIDADE = {"id", "nome", "uf"}
    CHAVES_FOTO = {"imagem", "ordem", "capa"}
    CHAVES_CARACTERISTICA = {"id", "nome"}
    CHAVES_CASA = {
        "area_terreno", "area_construida", "area_livre", "quartos", "suites", "banheiros",
        "salas", "vagas", "pavimentos", "ano_construcao", "piscina", "quintal",
        "churrasqueira", "mobiliada",
    }
    CHAVES_TERRENO = {
        "area_total", "frente", "fundo", "lateral_esquerda", "lateral_direita", "topografia",
        "murado", "esquina", "uso_permitido",
    }
    CHAVES_APARTAMENTO = {
        "predio", "andar", "area_privativa", "area_total", "quartos", "suites", "banheiros",
        "vagas", "varanda", "posicao_solar",
    }
    CHAVES_PREDIO = {
        "nome", "andares", "ano_construcao", "elevadores", "portaria_24h", "piscina",
        "academia", "salao_de_festas", "playground",
    }
    CHAVES_LOTE = {
        "numero", "area", "frente", "fundo", "entrada_minima", "parcelas_maximas",
        "condicoes_parcelamento", "quadra",
    }
    CHAVES_QUADRA = {"identificacao", "loteamento"}
    CHAVES_LOTEAMENTO = {
        "nome", "incorporadora", "asfalto", "agua", "energia", "esgoto", "iluminacao",
        "areas_de_lazer",
    }
    # Task 2 do 05-02 (opcao-a): empresa_id fica no perfil público da W02. Pendente para a
    # frente web decidir (revisão de exposição de contato do corretor, §11.5 g, também adiada).
    CHAVES_CORRETOR = {
        "id", "empresa_id", "nome", "foto", "email", "creci", "telefone", "whatsapp",
        "apresentacao",
    }

    def setUp(self):
        super().setUp()
        self.imovel_lote, self.lote = publicar_lote_da_vitrine(self)
        FotoImovel.objects.create(imovel=self.imovel_lote, imagem=gerar_imagem(), ordem=0)

    def detalhe(self, imovel):
        response = self.client.get(reverse("publico-imovel", args=[imovel.pk]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.json()

    def test_item_da_lista_de_toda_natureza_tem_as_chaves_exatas(self):
        itens = self.buscar().json()["results"]
        naturezas = {item["natureza"] for item in itens}
        self.assertEqual(naturezas, {"CASA", "APARTAMENTO", "TERRENO", "LOTE"})
        for item in itens:
            with self.subTest(id=item["id"], natureza=item["natureza"]):
                self.assertEqual(set(item), self.CHAVES_ITEM)

    def test_item_da_vitrine_do_corretor_e_da_empresa_tem_as_chaves_exatas(self):
        rotas = (
            reverse("publico-vitrine-corretor", args=[self.corretor.pk]),
            reverse("publico-vitrine-empresa", args=[self.empresa.pk]),
        )
        for rota in rotas:
            itens = self.client.get(rota).json()["results"]
            self.assertTrue(itens, rota)
            for item in itens:
                with self.subTest(rota=rota, id=item["id"]):
                    self.assertEqual(set(item), self.CHAVES_ITEM)

    def test_detalhe_de_toda_natureza_tem_as_chaves_exatas(self):
        for imovel in (self.casa, self.apto, self.terreno, self.imovel_lote):
            with self.subTest(natureza=imovel.natureza):
                dados = self.detalhe(imovel)
                self.assertEqual(set(dados), self.CHAVES_DETALHE)
                self.assertEqual(set(dados["endereco"]), self.CHAVES_ENDERECO)
                self.assertEqual(set(dados["endereco"]["cidade"]), self.CHAVES_CIDADE)
                self.assertEqual(set(dados["corretor_responsavel"]), self.CHAVES_CORRETOR)
                for foto in dados["fotos"]:
                    self.assertEqual(set(foto), self.CHAVES_FOTO)
                for caracteristica in dados["caracteristicas"]:
                    self.assertEqual(set(caracteristica), self.CHAVES_CARACTERISTICA)

    def test_bloco_da_natureza_tem_as_chaves_exatas_e_os_outros_vem_nulos(self):
        esperado = {
            "casa": (self.casa, self.CHAVES_CASA),
            "terreno": (self.terreno, self.CHAVES_TERRENO),
            "apartamento": (self.apto, self.CHAVES_APARTAMENTO),
            "lote": (self.imovel_lote, self.CHAVES_LOTE),
        }
        for bloco, (imovel, chaves) in esperado.items():
            with self.subTest(bloco=bloco):
                dados = self.detalhe(imovel)
                self.assertEqual(set(dados[bloco]), chaves)
                for outro in set(esperado) - {bloco}:
                    self.assertIsNone(dados[outro])

    def test_predio_quadra_e_loteamento_tem_as_chaves_exatas(self):
        self.assertEqual(set(self.detalhe(self.apto)["apartamento"]["predio"]), self.CHAVES_PREDIO)
        quadra = self.detalhe(self.imovel_lote)["lote"]["quadra"]
        self.assertEqual(set(quadra), self.CHAVES_QUADRA)
        self.assertEqual(set(quadra["loteamento"]), self.CHAVES_LOTEAMENTO)

    def test_lote_nao_vaza_vendido_id_cidade_nem_empresa(self):
        corpo = self.detalhe(self.imovel_lote)
        vazadas = todas_as_chaves(corpo) & VitrineSemTokenESemVazamentoTests.CHAVES_PROIBIDAS
        self.assertFalse(vazadas, vazadas)
        lote = corpo["lote"]
        for proibida in ("id", "vendido", "preco", "imovel", "empresa", "cidade"):
            self.assertNotIn(proibida, lote)
            self.assertNotIn(proibida, lote["quadra"])
            self.assertNotIn(proibida, lote["quadra"]["loteamento"])


class VitrineFotosCapaTests(VitrineBase):
    """A-02: a galeria do detalhe começa pela capa; a ordem é regra da API, o app não reordena."""

    def _fotos(self, imovel, capa_na_ordem=None):
        fotos = [
            FotoImovel.objects.create(imovel=imovel, imagem=gerar_imagem(), ordem=ordem)
            for ordem in (0, 1, 2)
        ]
        if capa_na_ordem is not None:
            fotos[capa_na_ordem].definir_como_capa()
        return fotos

    def _detalhe(self, imovel):
        return self.client.get(reverse("publico-imovel", args=[imovel.pk])).json()

    def _cartao(self, imovel):
        itens = self.buscar().json()["results"]
        return next(item for item in itens if item["id"] == imovel.pk)

    def test_capa_abre_a_galeria_e_o_resto_segue_ordem_e_id(self):
        self._fotos(self.sobrado, capa_na_ordem=2)
        fotos = self._detalhe(self.sobrado)["fotos"]
        self.assertEqual([f["capa"] for f in fotos], [True, False, False])
        self.assertEqual([f["ordem"] for f in fotos], [2, 0, 1])

    def test_foto_capa_do_cartao_e_a_primeira_foto_do_detalhe(self):
        self._fotos(self.sobrado, capa_na_ordem=2)
        detalhe = self._detalhe(self.sobrado)
        self.assertEqual(self._cartao(self.sobrado)["foto_capa"], detalhe["fotos"][0]["imagem"])

    def test_sem_capa_marcada_vale_ordem_e_id_e_a_capa_do_cartao_e_a_primeira(self):
        self._fotos(self.sobrado)
        detalhe = self._detalhe(self.sobrado)
        self.assertEqual([f["ordem"] for f in detalhe["fotos"]], [0, 1, 2])
        self.assertFalse(any(f["capa"] for f in detalhe["fotos"]))
        self.assertEqual(self._cartao(self.sobrado)["foto_capa"], detalhe["fotos"][0]["imagem"])

    def test_imovel_sem_fotos_tem_galeria_vazia_e_capa_nula(self):
        self.assertEqual(self._detalhe(self.sobrado)["fotos"], [])
        self.assertIsNone(self._cartao(self.sobrado)["foto_capa"])


# --- Opções de filtro públicas e multi-seleção (APP05, D-07/D-08) ------------


class VitrineOpcoesTests(VitrineBase):
    """D-08: bairros da cidade e catálogo de características, sem token."""

    def setUp(self):
        super().setUp()
        self.gestor = criar_gestor(self.empresa)
        # reservado, de bairro exclusivo: some da vitrine, então o bairro some das opções
        self.reservado = self._publicado("RESERVADO-1", titulo="Casa reservada", bairro="Tatuapé")
        self.reservado.mudar_situacao(Imovel.Situacao.RESERVADO, self.gestor)
        # bairro vazio ou ausente nunca é uma opção
        self._publicado("SEM-BAIRRO-1", titulo="Casa sem bairro", bairro=None)
        criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="BAIRRO-VAZIO-1",
            titulo="Casa de bairro vazio", endereco=criar_endereco(self.sp, ""), publicado=True,
        )

    def bairros(self, cidade=None):
        url = reverse("publico-bairros-cidade", args=[(cidade or self.sp).pk])
        return self.client.get(url)

    def test_bairros_da_cidade_ordenados_e_sem_repeticao(self):
        response = self.bairros()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Moema se repete (casa e terreno); o do apartamento é o do prédio
        self.assertEqual(response.json(), ["Bela Vista", "Moema", "Pinheiros"])

    def test_bairro_de_rascunho_reservado_empresa_inativa_ou_outra_cidade_nao_aparece(self):
        self.rascunho.endereco.bairro = "Bairro do Rascunho"
        self.rascunho.endereco.save()
        self.da_inativa.endereco.bairro = "Bairro da Inativa"
        self.da_inativa.endereco.save()
        corpo = self.bairros().json()
        for fora in ("Bairro do Rascunho", "Bairro da Inativa", "Tatuapé", "Centro", ""):
            self.assertNotIn(fora, corpo)
        self.assertEqual(self.bairros(self.campinas).json(), ["Centro"])

    def test_bairro_volta_quando_o_imovel_volta_para_a_vitrine(self):
        self.reservado.mudar_situacao(Imovel.Situacao.DISPONIVEL, self.gestor)
        self.assertIn("Tatuapé", self.bairros().json())

    def test_todo_bairro_oferecido_leva_a_uma_busca_nao_vazia(self):
        for bairro in self.bairros().json():
            with self.subTest(bairro=bairro):
                self.assertTrue(self.ids(self.buscar(bairro=bairro)))

    def test_cidade_inexistente_da_404_e_post_da_405(self):
        self.assertEqual(
            self.client.get(reverse("publico-bairros-cidade", args=[99999])).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        url = reverse("publico-bairros-cidade", args=[self.sp.pk])
        self.assertEqual(self.client.post(url, {}).status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_token_invalido_nao_muda_a_resposta(self):
        esperado = self.bairros().json()
        self.client.credentials(HTTP_AUTHORIZATION="Token invalido")
        response = self.bairros()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), esperado)

    def test_catalogo_de_caracteristicas_ordenado_por_nome_sem_token(self):
        Caracteristica.objects.create(nome="Piscina")  # nenhum imóvel usa: ainda é do catálogo
        response = self.client.get(reverse("publico-caracteristicas"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        corpo = response.json()
        self.assertIsInstance(corpo, list)  # sem envelope de paginação
        self.assertEqual(
            [item["nome"] for item in corpo], ["Ar-condicionado", "Piscina", "Portão eletrônico"]
        )
        for item in corpo:
            self.assertEqual(set(item), {"id", "nome"})
        self.assertEqual(corpo[0]["id"], self.ar.pk)

    def test_catalogo_so_aceita_leitura_e_ignora_token_invalido(self):
        url = reverse("publico-caracteristicas")
        self.assertEqual(self.client.post(url, {"nome": "X"}).status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.client.credentials(HTTP_AUTHORIZATION="Token invalido")
        self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)


class VitrineMultiSelecaoTests(VitrineBase):
    """D-07: natureza e bairro aceitam vários valores separados por vírgula (OU)."""

    def setUp(self):
        super().setUp()
        self.imovel_lote, _ = publicar_lote_da_vitrine(self)

    def test_natureza_csv_traz_casas_e_lotes(self):
        self.assertCountEqual(
            self.ids(self.buscar(natureza="CASA,LOTE")),
            [self.casa.pk, self.sobrado.pk, self.imovel_lote.pk],
        )

    def test_natureza_unica_continua_funcionando(self):
        self.assertEqual(self.ids(self.buscar(natureza="LOTE")), [self.imovel_lote.pk])
        self.assertEqual(self.ids(self.buscar(natureza="TERRENO")), [self.terreno.pk])

    def test_natureza_com_valor_invalido_da_400_com_a_chave_natureza(self):
        response = self.buscar(natureza="CASA,XYZ")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(set(response.data), {"natureza"})
        self.assertIn("Opções válidas", str(response.data["natureza"]))

    def test_natureza_com_espacos_vale_como_o_csv_aparado(self):
        self.assertCountEqual(
            self.ids(self.buscar(natureza=" CASA , LOTE ")),
            self.ids(self.buscar(natureza="CASA,LOTE")),
        )

    def test_natureza_so_com_virgula_vale_como_sem_filtro(self):
        self.assertCountEqual(self.ids(self.buscar(natureza=",")), self.ids(self.buscar()))

    def test_bairro_csv_traz_os_dois_bairros_sem_diferenciar_maiusculas(self):
        self.assertCountEqual(
            self.ids(self.buscar(bairro="MOEMA,pinheiros")),
            [self.casa.pk, self.terreno.pk, self.sobrado.pk],
        )
        # o bairro do apartamento é o do prédio
        self.assertCountEqual(
            self.ids(self.buscar(bairro="bela vista, Vila Nova")), [self.apto.pk, self.imovel_lote.pk]
        )

    def test_bairro_unico_continua_funcionando(self):
        self.assertCountEqual(self.ids(self.buscar(bairro="moema")), [self.casa.pk, self.terreno.pk])

    def test_bairro_csv_sem_itens_vale_como_sem_filtro(self):
        self.assertCountEqual(self.ids(self.buscar(bairro=" , ")), self.ids(self.buscar()))

    def test_bairro_csv_respeita_a_cidade(self):
        # "Centro" só existe em Campinas: em São Paulo não traz nada
        self.assertEqual(self.ids(self.buscar(bairro="Centro,Inexistente")), [])
        self.assertEqual(self.ids(self.buscar(cidade=self.campinas, bairro="centro,x")), [self.em_campinas.pk])

    def test_bairro_com_virgula_no_nome_nao_e_representavel(self):
        """Risco aceito (A-05): a vírgula separa itens, então 'Vila, Nova' vira dois bairros."""
        self.casa.endereco.bairro = "Vila, Nova"
        self.casa.endereco.save()
        self.assertNotIn(self.casa.pk, self.ids(self.buscar(bairro="Vila, Nova")))


def endereco_completo(**extra):
    cidade = Cidade.objects.get_or_create(nome="São Paulo", uf="SP")[0]
    dados = dict(
        cep="01000-000", logradouro="Rua A", numero="10", bairro="Centro", cidade=cidade,
        latitude="-23.55", longitude="-46.63",
    )
    dados.update(extra)
    return Endereco.objects.create(**dados)


def gerar_pdf(nome="matricula.pdf"):
    return SimpleUploadedFile(nome, b"%PDF-1.4\n%fake\n", content_type="application/pdf")


def dados_documento(imovel=None, **extra):
    dados = {"tipo": DocumentoImovel.Tipo.MATRICULA, "arquivo": gerar_pdf(), "data_documento": "2026-01-15"}
    if imovel is not None:
        dados["imovel"] = imovel.pk
    dados.update(extra)
    return dados


class MediaTemporariaMixin:
    """Arquivos enviados nos testes vão pra uma pasta temporária, apagada no fim."""

    @classmethod
    def setUpClass(cls):
        cls._media = tempfile.mkdtemp()
        cls._override = override_settings(MEDIA_ROOT=cls._media)
        cls._override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._override.disable()
        shutil.rmtree(cls._media, ignore_errors=True)


class SituacaoPublicacaoModelTests(TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.gestor = criar_gestor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)

    def test_imovel_nasce_disponivel_e_fora_da_vitrine(self):
        self.assertEqual(self.imovel.situacao, Imovel.Situacao.DISPONIVEL)
        self.assertFalse(self.imovel.publicado)
        self.assertEqual(self.imovel.situacao_desde, self.imovel.criado_em)

    def test_mudar_situacao_grava_historico_completo(self):
        historico = self.imovel.mudar_situacao(
            Imovel.Situacao.RESERVADO, self.gestor, "Reservado para o cliente João"
        )
        self.imovel.refresh_from_db()
        self.assertEqual(self.imovel.situacao, Imovel.Situacao.RESERVADO)
        self.assertEqual(historico.situacao_anterior, Imovel.Situacao.DISPONIVEL)
        self.assertEqual(historico.situacao_nova, Imovel.Situacao.RESERVADO)
        self.assertEqual(historico.alterado_por, self.gestor)
        self.assertEqual(historico.observacao, "Reservado para o cliente João")
        self.assertIsNotNone(historico.alterado_em)

    def test_historico_responde_desde_quando_esta_reservado(self):
        historico = self.imovel.mudar_situacao(Imovel.Situacao.RESERVADO, self.gestor)
        self.assertEqual(self.imovel.situacao_desde, historico.alterado_em)

    def test_cada_mudanca_vira_uma_linha_no_historico(self):
        self.imovel.mudar_situacao(Imovel.Situacao.RESERVADO, self.gestor)
        self.imovel.mudar_situacao(Imovel.Situacao.VENDIDO, self.gestor)
        caminhos = [
            (h.situacao_anterior, h.situacao_nova)
            for h in self.imovel.historico_situacao.order_by("id")
        ]
        self.assertEqual(caminhos, [("DISPONIVEL", "RESERVADO"), ("RESERVADO", "VENDIDO")])

    def test_mudar_para_a_mesma_situacao_e_recusado(self):
        with self.assertRaises(ValidationError):
            self.imovel.mudar_situacao(Imovel.Situacao.DISPONIVEL, self.gestor)
        self.assertFalse(self.imovel.historico_situacao.exists())

    def test_situacao_invalida_e_recusada(self):
        with self.assertRaises(ValidationError):
            self.imovel.mudar_situacao("DEMOLIDO", self.gestor)

    def test_situacao_nao_aparece_no_formulario_do_corretor(self):
        from .forms import ImovelForm

        self.assertNotIn("situacao", ImovelForm().fields)

    def test_publicar_sem_endereco_diz_que_falta_o_endereco(self):
        with self.assertRaises(ValidationError) as contexto:
            self.imovel.publicar()
        self.assertIn("falta o endereço", contexto.exception.messages[0])

    def test_publicar_com_endereco_incompleto_diz_o_que_falta(self):
        self.imovel.endereco = endereco_completo(cep="", bairro="", latitude=None)
        self.imovel.save()
        with self.assertRaises(ValidationError) as contexto:
            self.imovel.publicar()
        mensagem = contexto.exception.messages[0]
        self.assertIn("CEP", mensagem)
        self.assertIn("bairro", mensagem)
        self.assertIn("coordenadas", mensagem)
        self.assertNotIn("logradouro", mensagem)
        self.assertFalse(Imovel.objects.get(pk=self.imovel.pk).publicado)

    def test_apartamento_sem_predio_diz_que_falta_o_predio(self):
        apto = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="AP1",
            natureza=Imovel.Natureza.APARTAMENTO,
        )
        self.assertEqual(apto.pendencias_para_publicar(), ["o prédio do apartamento (o endereço vem dele)"])

    def test_publicar_e_tirar_da_vitrine(self):
        self.imovel.endereco = endereco_completo()
        self.imovel.save()
        self.imovel.publicar()
        self.assertTrue(Imovel.objects.get(pk=self.imovel.pk).publicado)
        self.imovel.despublicar()
        self.assertFalse(Imovel.objects.get(pk=self.imovel.pk).publicado)

    def test_vitrine_so_mostra_disponivel_e_publicado(self):
        no_ar = criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="NOAR", publicado=True)
        criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="RASCUNHO")
        reservado = criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="RES", publicado=True)
        reservado.mudar_situacao(Imovel.Situacao.RESERVADO, self.gestor)
        alugado = criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="ALU", publicado=True)
        alugado.mudar_situacao(Imovel.Situacao.ALUGADO, self.gestor)

        self.assertEqual(list(Imovel.objects.na_vitrine()), [no_ar])

    def test_imovel_volta_para_a_vitrine_quando_fica_disponivel_de_novo(self):
        imovel = criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="VOLTA", publicado=True)
        imovel.mudar_situacao(Imovel.Situacao.RESERVADO, self.gestor)
        self.assertNotIn(imovel, Imovel.objects.na_vitrine())
        imovel.mudar_situacao(Imovel.Situacao.DISPONIVEL, self.gestor, "Reserva desfeita")
        self.assertIn(imovel, Imovel.objects.na_vitrine())

    def test_vitrine_nao_mostra_imovel_de_empresa_inativa(self):
        criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="NOAR", publicado=True)
        self.empresa.ativa = False
        self.empresa.save()
        self.assertFalse(Imovel.objects.na_vitrine().exists())

    def test_lote_vendido_conta_na_quadra(self):
        loteamento = criar_loteamento(self.empresa)
        quadra = Quadra.objects.create(loteamento=loteamento, identificacao="A")
        vendido = criar_imovel_lote(self.empresa, self.corretor, self.proprietario, "L1")
        livre = criar_imovel_lote(self.empresa, self.corretor, self.proprietario, "L2")
        criar_lote(vendido, quadra, numero="1")
        criar_lote(livre, quadra, numero="2")

        vendido.mudar_situacao(Imovel.Situacao.VENDIDO, self.gestor)

        self.assertEqual(quadra.resumo_lotes(), {"total": 2, "disponiveis": 1, "vendidos": 1})


class DocumentoModelTests(MediaTemporariaMixin, TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.gestor = criar_gestor(self.empresa)
        self.imovel = criar_imovel(self.empresa, self.corretor, criar_proprietario(self.empresa))

    def _documento(self, arquivo, **extra):
        dados = dict(
            imovel=self.imovel, tipo=DocumentoImovel.Tipo.IPTU, arquivo=arquivo,
            data_documento=date(2026, 1, 15), enviado_por=self.gestor,
        )
        dados.update(extra)
        return DocumentoImovel(**dados)

    def test_aceita_pdf(self):
        self._documento(gerar_pdf()).full_clean()

    def test_aceita_imagem(self):
        self._documento(gerar_imagem()).full_clean()

    def test_recusa_arquivo_que_nao_e_pdf_nem_imagem(self):
        falso = SimpleUploadedFile("matricula.pdf", b"isto nao e um pdf", content_type="application/pdf")
        with self.assertRaises(ValidationError) as contexto:
            self._documento(falso).full_clean()
        self.assertIn("arquivo", contexto.exception.message_dict)

    def test_recusa_data_no_futuro(self):
        with self.assertRaises(ValidationError) as contexto:
            self._documento(gerar_pdf(), data_documento=date(2999, 1, 1)).full_clean()
        self.assertIn("data_documento", contexto.exception.message_dict)

    def test_nome_do_arquivo_nao_revela_o_original(self):
        documento = self._documento(gerar_pdf("matricula-do-joao.pdf"))
        documento.save()
        self.assertNotIn("joao", documento.arquivo.name)
        self.assertTrue(documento.arquivo.name.endswith(".pdf"))


class PainelGestaoTests(MediaTemporariaMixin, TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.gestor = criar_gestor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        self.de_fora = criar_imovel(
            outra_empresa, criar_corretor(outra_empresa, email="fora@teste.com"),
            criar_proprietario(outra_empresa, documento="10902516612"), codigo="FORA",
        )
        self.client.force_login(self.gestor)

    def url(self, nome, *args):
        return reverse(f"imoveis:{nome}", args=[self.imovel.pk, *args])

    def test_corretor_nao_acessa_a_gestao(self):
        self.client.force_login(self.corretor)
        self.assertEqual(self.client.get(reverse("imoveis:gestao_imovel_list")).status_code, 403)
        self.assertEqual(self.client.post(self.url("gestao_imovel_publicar")).status_code, 403)
        self.assertEqual(self.client.get(self.url("gestao_imovel_detail")).status_code, 403)

    def test_corretor_nao_tem_mais_o_botao_publicar(self):
        self.client.force_login(self.corretor)
        response = self.client.get(reverse("imoveis:imovel_detail", args=[self.imovel.pk]))
        self.assertNotContains(response, "/publicar/")
        self.assertContains(response, "controlados pelo gestor")

    def test_lista_so_imoveis_da_empresa(self):
        response = self.client.get(reverse("imoveis:gestao_imovel_list"))
        self.assertEqual([i.codigo for i in response.context["imoveis"]], ["IM001"])

    def test_lista_filtra_por_situacao(self):
        outro = criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="IM002")
        outro.mudar_situacao(Imovel.Situacao.RESERVADO, self.gestor)
        response = self.client.get(reverse("imoveis:gestao_imovel_list") + "?situacao=RESERVADO")
        self.assertEqual([i.codigo for i in response.context["imoveis"]], ["IM002"])

    def test_imovel_de_outra_empresa_da_404(self):
        response = self.client.get(reverse("imoveis:gestao_imovel_detail", args=[self.de_fora.pk]))
        self.assertEqual(response.status_code, 404)

    def test_publicar_com_endereco_incompleto_mostra_o_que_falta(self):
        self.imovel.endereco = endereco_completo(bairro="", numero="")
        self.imovel.save()
        response = self.client.post(self.url("gestao_imovel_publicar"), follow=True)
        self.assertContains(response, "falta número e bairro")
        self.assertFalse(Imovel.objects.get(pk=self.imovel.pk).publicado)

    def test_tela_mostra_pendencias_antes_de_publicar(self):
        response = self.client.get(self.url("gestao_imovel_detail"))
        self.assertContains(response, "Para publicar, falta: o endereço")

    def test_interruptor_publica_e_tira_da_vitrine(self):
        self.imovel.endereco = endereco_completo()
        self.imovel.save()
        self.client.post(self.url("gestao_imovel_publicar"))
        self.assertTrue(Imovel.objects.get(pk=self.imovel.pk).publicado)
        self.client.post(self.url("gestao_imovel_despublicar"))
        self.assertFalse(Imovel.objects.get(pk=self.imovel.pk).publicado)

    def test_muda_situacao_e_grava_historico(self):
        response = self.client.post(self.url("gestao_imovel_situacao"), {
            "situacao": Imovel.Situacao.RESERVADO, "observacao": "Sinal pago",
        })
        self.assertRedirects(response, self.url("gestao_imovel_detail"))
        historico = self.imovel.historico_situacao.get()
        self.assertEqual(historico.alterado_por, self.gestor)
        self.assertEqual(historico.observacao, "Sinal pago")

        response = self.client.get(self.url("gestao_imovel_detail"))
        self.assertContains(response, "Reservado</strong> desde")
        self.assertContains(response, "Sinal pago")

    def test_mesma_situacao_mostra_erro(self):
        response = self.client.post(self.url("gestao_imovel_situacao"), {
            "situacao": Imovel.Situacao.DISPONIVEL,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "já está disponível")

    def test_anexa_documento_e_baixa(self):
        response = self.client.post(self.url("gestao_documento_enviar"), dados_documento(data_documento="15/01/2026"))
        self.assertRedirects(response, self.url("gestao_imovel_detail"))
        documento = self.imovel.documentos.get()
        self.assertEqual(documento.tipo, DocumentoImovel.Tipo.MATRICULA)
        self.assertEqual(documento.enviado_por, self.gestor)
        self.assertEqual(str(documento.data_documento), "2026-01-15")

        response = self.client.get(self.url("gestao_documento_baixar", documento.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4\n%fake\n")

    def test_documento_invalido_mostra_erro(self):
        falso = SimpleUploadedFile("x.pdf", b"nada", content_type="application/pdf")
        response = self.client.post(
            self.url("gestao_documento_enviar"),
            dados_documento(arquivo=falso, data_documento="15/01/2026"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Use PDF, JPG ou PNG")
        self.assertFalse(self.imovel.documentos.exists())

    def test_campo_de_data_do_documento_e_dia_mes_ano(self):
        response = self.client.get(self.url("gestao_imovel_detail"))
        self.assertContains(response, 'placeholder="dd/mm/aaaa"')
        self.assertNotContains(response, 'type="date"')
        # máscara que aceita só números e põe as barras (static/js/mascaras.js)
        self.assertContains(response, 'data-mascara="data"')
        self.assertContains(response, "js/mascaras.js")

    def test_data_fora_do_formato_dia_mes_ano_mostra_erro(self):
        response = self.client.post(
            self.url("gestao_documento_enviar"), dados_documento(data_documento="2026-01-15")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Informe a data no formato dd/mm/aaaa")
        self.assertFalse(self.imovel.documentos.exists())

    def test_data_mes_dia_nao_e_aceita_como_dia_mes(self):
        # 01/15/2026 (mm/dd) não existe em dd/mm: é recusado, não vira outra data
        response = self.client.post(
            self.url("gestao_documento_enviar"), dados_documento(data_documento="01/15/2026")
        )
        self.assertContains(response, "Informe a data no formato dd/mm/aaaa")

    def test_nao_baixa_documento_de_outra_empresa(self):
        outro_gestor = criar_gestor(self.de_fora.empresa, email="gestor-fora@teste.com")
        documento = DocumentoImovel.objects.create(
            imovel=self.de_fora, tipo=DocumentoImovel.Tipo.IPTU, arquivo=gerar_pdf(),
            data_documento=date(2026, 1, 1), enviado_por=outro_gestor,
        )
        response = self.client.get(
            reverse("imoveis:gestao_documento_baixar", args=[self.de_fora.pk, documento.pk])
        )
        self.assertEqual(response.status_code, 404)
        # nem trocando só o id do documento, pendurado num imóvel da própria empresa
        response = self.client.get(self.url("gestao_documento_baixar", documento.pk))
        self.assertEqual(response.status_code, 404)


class ApiGestaoTests(MediaTemporariaMixin, APITestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.gestor = criar_gestor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)
        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        self.de_fora = criar_imovel(
            outra_empresa, criar_corretor(outra_empresa, email="fora@teste.com"),
            criar_proprietario(outra_empresa, documento="10902516612"), codigo="FORA",
        )
        self.client.force_authenticate(self.gestor)

    def test_corretor_nao_acessa_gestao(self):
        self.client.force_authenticate(self.corretor)
        self.assertEqual(
            self.client.get(reverse("gestao-imovel-list")).status_code, status.HTTP_403_FORBIDDEN
        )
        self.assertEqual(
            self.client.get(reverse("gestao-documento-list")).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_corretor_nao_publica_mais_pela_api(self):
        self.client.force_authenticate(self.corretor)
        response = self.client.post(f"/api/imoveis/{self.imovel.pk}/publicar/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_corretor_nao_muda_situacao_nem_publicacao_editando_o_imovel(self):
        self.client.force_authenticate(self.corretor)
        self.client.patch(reverse("imovel-detail", args=[self.imovel.pk]), {
            "situacao": "VENDIDO", "publicado": True,
        }, format="json")
        self.imovel.refresh_from_db()
        self.assertEqual(self.imovel.situacao, Imovel.Situacao.DISPONIVEL)
        self.assertFalse(self.imovel.publicado)

    def test_gestor_lista_so_da_empresa(self):
        response = self.client.get(reverse("gestao-imovel-list"))
        self.assertEqual([i["codigo"] for i in response.data], ["IM001"])

    def test_imovel_de_outra_empresa_da_404(self):
        response = self.client.post(reverse("gestao-imovel-publicar", args=[self.de_fora.pk]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_publicar_incompleto_lista_o_que_falta(self):
        self.imovel.endereco = endereco_completo(cep="")
        self.imovel.save()
        response = self.client.post(reverse("gestao-imovel-publicar", args=[self.imovel.pk]))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["pendencias"], ["CEP"])
        self.assertIn("falta CEP", response.data["detail"])

    def test_publicar_e_despublicar(self):
        self.imovel.endereco = endereco_completo()
        self.imovel.save()
        response = self.client.post(reverse("gestao-imovel-publicar", args=[self.imovel.pk]))
        self.assertTrue(response.data["publicado"])
        response = self.client.post(reverse("gestao-imovel-despublicar", args=[self.imovel.pk]))
        self.assertFalse(response.data["publicado"])

    def test_mudar_situacao_e_ver_historico(self):
        response = self.client.post(reverse("gestao-imovel-situacao", args=[self.imovel.pk]), {
            "situacao": "RESERVADO", "observacao": "Sinal pago",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["situacao"], "RESERVADO")

        response = self.client.get(reverse("gestao-imovel-historico", args=[self.imovel.pk]))
        self.assertEqual(len(response.data), 1)
        mudanca = response.data[0]
        self.assertEqual(mudanca["situacao_anterior"], "DISPONIVEL")
        self.assertEqual(mudanca["situacao_nova"], "RESERVADO")
        self.assertEqual(mudanca["alterado_por"], "Gestor Teste")
        self.assertEqual(mudanca["observacao"], "Sinal pago")
        self.assertIsNotNone(mudanca["alterado_em"])

    def test_mesma_situacao_e_recusada(self):
        response = self.client.post(reverse("gestao-imovel-situacao", args=[self.imovel.pk]), {
            "situacao": "DISPONIVEL",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_envia_lista_e_baixa_documento(self):
        response = self.client.post(
            reverse("gestao-documento-list"), dados_documento(self.imovel), format="multipart"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["enviado_por"], "Gestor Teste")
        self.assertNotIn("arquivo", response.data)

        lista = self.client.get(reverse("gestao-documento-list") + f"?imovel={self.imovel.pk}")
        self.assertEqual(len(lista.data), 1)

        baixar = self.client.get(reverse("gestao-documento-baixar", args=[response.data["id"]]))
        self.assertEqual(baixar.status_code, status.HTTP_200_OK)
        self.assertEqual(b"".join(baixar.streaming_content), b"%PDF-1.4\n%fake\n")

    def test_nao_anexa_em_imovel_de_outra_empresa(self):
        response = self.client.post(
            reverse("gestao-documento-list"), dados_documento(self.de_fora), format="multipart"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("imovel", response.data)

    def test_documento_invalido_e_recusado(self):
        falso = SimpleUploadedFile("x.pdf", b"nada", content_type="application/pdf")
        response = self.client.post(
            reverse("gestao-documento-list"), dados_documento(self.imovel, arquivo=falso),
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("arquivo", response.data)


class VitrinePublicaTests(APITestCase):
    """Regra da W08 aplicada à vitrine pública da W09 (rotas publico-vitrine-cidade e publico-imovel):
    só imóvel disponível E publicado."""

    def setUp(self):
        # o limite de taxa guarda o histórico no cache; cada teste começa do zero e com taxa alta fixa,
        # então a suíte não depende da velocidade da máquina nem do VITRINE_TAXA_ANONIMA do `.env`
        cache.clear()
        fixar_taxa_alta(self)
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.gestor = criar_gestor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.no_ar = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="NOAR", publicado=True,
            endereco=endereco_completo(),
        )
        self.cidade = self.no_ar.endereco.cidade
        self.rascunho = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="RASCUNHO",
            endereco=endereco_completo(),
        )
        self.reservado = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="RES", publicado=True,
            endereco=endereco_completo(),
        )
        self.reservado.mudar_situacao(Imovel.Situacao.RESERVADO, self.gestor)

    def listar(self):
        return self.client.get(reverse("publico-vitrine-cidade", args=[self.cidade.pk]))

    def test_vitrine_nao_exige_login(self):
        response = self.listar()
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_vitrine_so_lista_disponivel_e_publicado(self):
        response = self.listar()
        self.assertEqual([i["id"] for i in response.data["results"]], [self.no_ar.pk])

    def test_detalhe_de_imovel_fora_da_vitrine_da_404(self):
        for imovel in (self.rascunho, self.reservado):
            response = self.client.get(reverse("publico-imovel", args=[imovel.pk]))
            self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_imovel_vendido_ou_alugado_sai_da_vitrine(self):
        for situacao in (Imovel.Situacao.VENDIDO, Imovel.Situacao.ALUGADO):
            with self.subTest(situacao=situacao):
                self.no_ar.mudar_situacao(situacao, self.gestor)
                self.assertEqual(self.listar().data["results"], [])
                response = self.client.get(reverse("publico-imovel", args=[self.no_ar.pk]))
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_vitrine_nao_expoe_dados_internos(self):
        response = self.client.get(reverse("publico-imovel", args=[self.no_ar.pk]))
        for campo in (
            "codigo", "proprietario", "situacao", "publicado", "pendencias_para_publicar", "documentos",
        ):
            self.assertNotIn(campo, response.data)
        self.assertEqual(response.data["endereco"]["bairro"], "Centro")

    def test_vitrine_e_so_leitura(self):
        for url in (
            reverse("publico-vitrine-cidade", args=[self.cidade.pk]),
            reverse("publico-imovel", args=[self.no_ar.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, {"titulo": "X"}).status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class FiltrosListaImoveisTests(TestCase):
    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.outro_corretor = criar_corretor(self.empresa, email="outro@teste.com")
        self.gestor = criar_gestor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)

        self.casa = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="CASA1", titulo="Casa no centro",
            publicado=True,
        )
        self.terreno = criar_imovel(
            self.empresa, self.outro_corretor, self.proprietario, codigo="TER1", titulo="Terreno plano",
            natureza=Imovel.Natureza.TERRENO, finalidade=Imovel.Finalidade.ALUGUEL,
            preco_venda=None, preco_aluguel="900.00",
        )
        self.reservado = criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="CASA2", publicado=True,
        )
        self.reservado.mudar_situacao(Imovel.Situacao.RESERVADO, self.gestor)

        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        self.corretor_de_fora = criar_corretor(outra_empresa, email="fora@teste.com")
        criar_imovel(
            outra_empresa, self.corretor_de_fora,
            criar_proprietario(outra_empresa, documento="10902516612"), codigo="FORA",
        )

    def codigos(self, url, **filtros):
        response = self.client.get(url, filtros)
        self.assertEqual(response.status_code, 200)
        return sorted(i.codigo for i in response.context["imoveis"])

    # ---------------------------------------------------------------- gestor
    def test_gestor_filtra_por_natureza(self):
        self.client.force_login(self.gestor)
        url = reverse("imoveis:gestao_imovel_list")
        self.assertEqual(self.codigos(url, natureza="TERRENO"), ["TER1"])

    def test_gestor_filtra_por_corretor(self):
        self.client.force_login(self.gestor)
        url = reverse("imoveis:gestao_imovel_list")
        self.assertEqual(self.codigos(url, corretor=self.outro_corretor.pk), ["TER1"])

    def test_gestor_so_ve_corretores_da_empresa_no_filtro(self):
        self.client.force_login(self.gestor)
        response = self.client.get(reverse("imoveis:gestao_imovel_list"))
        opcoes = list(response.context["filtro"].fields["corretor"].queryset)
        self.assertEqual(sorted(c.email for c in opcoes), ["corretor@teste.com", "outro@teste.com"])

    def test_corretor_de_outra_empresa_na_url_nao_vaza_nada(self):
        self.client.force_login(self.gestor)
        url = reverse("imoveis:gestao_imovel_list")
        # filtro inválido é ignorado: continua mostrando só os da própria empresa
        self.assertEqual(self.codigos(url, corretor=self.corretor_de_fora.pk), ["CASA1", "CASA2", "TER1"])

    def test_gestor_filtra_por_vitrine(self):
        self.client.force_login(self.gestor)
        url = reverse("imoveis:gestao_imovel_list")
        self.assertEqual(self.codigos(url, vitrine="NA_VITRINE"), ["CASA1"])
        self.assertEqual(self.codigos(url, vitrine="PUBLICADO_FORA"), ["CASA2"])
        self.assertEqual(self.codigos(url, vitrine="NAO_PUBLICADO"), ["TER1"])

    def test_filtro_na_vitrine_bate_com_a_vitrine_publica(self):
        self.client.force_login(self.gestor)
        url = reverse("imoveis:gestao_imovel_list")
        vitrine = sorted(Imovel.objects.na_vitrine().filter(empresa=self.empresa).values_list("codigo", flat=True))
        self.assertEqual(self.codigos(url, vitrine="NA_VITRINE"), vitrine)

    def test_gestor_combina_filtros(self):
        self.client.force_login(self.gestor)
        url = reverse("imoveis:gestao_imovel_list")
        self.assertEqual(
            self.codigos(url, natureza="CASA", corretor=self.corretor.pk, situacao="RESERVADO"), ["CASA2"]
        )

    def test_gestor_busca_por_codigo_ou_titulo(self):
        self.client.force_login(self.gestor)
        url = reverse("imoveis:gestao_imovel_list")
        self.assertEqual(self.codigos(url, busca="ter1"), ["TER1"])
        self.assertEqual(self.codigos(url, busca="centro"), ["CASA1"])

    # ---------------------------------------------------------------- corretor
    def test_corretor_tem_os_filtros_das_suas_colunas(self):
        self.client.force_login(self.corretor)
        response = self.client.get(reverse("imoveis:imovel_list"))
        self.assertEqual(
            list(response.context["filtro"].fields),
            ["busca", "natureza", "finalidade", "situacao", "vitrine"],
        )

    def test_corretor_filtra_por_finalidade(self):
        self.client.force_login(self.corretor)
        self.assertEqual(self.codigos(reverse("imoveis:imovel_list"), finalidade="ALUGUEL"), ["TER1"])

    def test_corretor_filtra_por_situacao_e_vitrine(self):
        self.client.force_login(self.corretor)
        url = reverse("imoveis:imovel_list")
        self.assertEqual(self.codigos(url, situacao="RESERVADO"), ["CASA2"])
        self.assertEqual(self.codigos(url, vitrine="NA_VITRINE"), ["CASA1"])

    def test_lista_do_corretor_mostra_situacao_e_vitrine(self):
        self.client.force_login(self.corretor)
        response = self.client.get(reverse("imoveis:imovel_list"))
        self.assertContains(response, "Na vitrine")
        self.assertContains(response, "Publicado, mas reservado")
        self.assertContains(response, "Não publicado")

    def test_sem_resultado_avisa_que_e_por_causa_do_filtro(self):
        self.client.force_login(self.corretor)
        response = self.client.get(reverse("imoveis:imovel_list"), {"busca": "nada-disso"})
        self.assertContains(response, "Nenhum imóvel com esses filtros")
        self.assertContains(response, "Limpar")


class CorretorMudaSituacaoTests(APITestCase):
    """O corretor responsável muda a situação do imóvel, com histórico; outros corretores não."""

    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.colega = criar_corretor(self.empresa, email="colega@teste.com")
        self.gestor = criar_gestor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        self.imovel = criar_imovel(self.empresa, self.corretor, self.proprietario)

        outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        self.corretor_de_fora = criar_corretor(outra_empresa, email="fora@teste.com")
        self.de_fora = criar_imovel(
            outra_empresa, self.corretor_de_fora,
            criar_proprietario(outra_empresa, documento="10902516612"), codigo="FORA",
        )

    # ---------------------------------------------------------------- regra (model)
    def test_quem_pode_mudar_a_situacao(self):
        self.assertTrue(self.imovel.pode_mudar_situacao(self.corretor))
        self.assertTrue(self.imovel.pode_mudar_situacao(self.gestor))
        self.assertFalse(self.imovel.pode_mudar_situacao(self.colega))
        self.assertFalse(self.imovel.pode_mudar_situacao(self.corretor_de_fora))
        gestor_de_fora = criar_gestor(self.de_fora.empresa, email="gestor-fora@teste.com")
        self.assertFalse(self.imovel.pode_mudar_situacao(gestor_de_fora))

    def test_corretor_responsavel_muda_para_qualquer_situacao(self):
        for situacao in (Imovel.Situacao.RESERVADO, Imovel.Situacao.VENDIDO,
                         Imovel.Situacao.ALUGADO, Imovel.Situacao.DISPONIVEL):
            self.imovel.mudar_situacao(situacao, self.corretor)
            self.assertEqual(Imovel.objects.get(pk=self.imovel.pk).situacao, situacao)
        self.assertEqual(self.imovel.historico_situacao.count(), 4)
        self.assertTrue(all(h.alterado_por == self.corretor for h in self.imovel.historico_situacao.all()))

    def test_colega_nao_muda_a_situacao_nem_pelo_model(self):
        with self.assertRaises(ValidationError):
            self.imovel.mudar_situacao(Imovel.Situacao.RESERVADO, self.colega)
        self.assertEqual(Imovel.objects.get(pk=self.imovel.pk).situacao, Imovel.Situacao.DISPONIVEL)
        self.assertFalse(self.imovel.historico_situacao.exists())

    # ---------------------------------------------------------------- painel
    def test_painel_corretor_responsavel_muda_situacao_com_historico(self):
        self.client.force_login(self.corretor)
        url = reverse("imoveis:imovel_mudar_situacao", args=[self.imovel.pk])
        response = self.client.post(url, {"situacao": "RESERVADO", "observacao": "Sinal pago"})
        self.assertRedirects(response, reverse("imoveis:imovel_detail", args=[self.imovel.pk]))

        historico = self.imovel.historico_situacao.get()
        self.assertEqual(historico.alterado_por, self.corretor)
        self.assertEqual(historico.observacao, "Sinal pago")

        response = self.client.get(reverse("imoveis:imovel_detail", args=[self.imovel.pk]))
        self.assertContains(response, "Reservado</strong> desde")
        self.assertContains(response, "Sinal pago")
        self.assertContains(response, "Mudar situação")

    def test_painel_colega_ve_situacao_mas_sem_formulario(self):
        self.client.force_login(self.colega)
        response = self.client.get(reverse("imoveis:imovel_detail", args=[self.imovel.pk]))
        self.assertContains(response, "Disponível</strong> desde")
        self.assertNotContains(response, "Mudar situação")
        self.assertContains(response, "Só o corretor responsável")

    def test_painel_colega_nao_muda_situacao(self):
        self.client.force_login(self.colega)
        response = self.client.post(
            reverse("imoveis:imovel_mudar_situacao", args=[self.imovel.pk]), {"situacao": "VENDIDO"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Imovel.objects.get(pk=self.imovel.pk).situacao, Imovel.Situacao.DISPONIVEL)

    def test_painel_imovel_de_outra_empresa_da_404(self):
        self.client.force_login(self.corretor)
        response = self.client.post(
            reverse("imoveis:imovel_mudar_situacao", args=[self.de_fora.pk]), {"situacao": "VENDIDO"}
        )
        self.assertEqual(response.status_code, 404)

    def test_painel_mesma_situacao_mostra_erro(self):
        self.client.force_login(self.corretor)
        response = self.client.post(
            reverse("imoveis:imovel_mudar_situacao", args=[self.imovel.pk]), {"situacao": "DISPONIVEL"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "já está disponível")

    def test_corretor_continua_sem_publicar(self):
        self.client.force_login(self.corretor)
        response = self.client.get(reverse("imoveis:imovel_detail", args=[self.imovel.pk]))
        self.assertNotContains(response, "/publicar/")
        self.assertContains(response, "Publicação e documentos são controlados pelo gestor")

    # ---------------------------------------------------------------- API
    def test_api_corretor_responsavel_muda_situacao(self):
        self.client.force_authenticate(self.corretor)
        response = self.client.post(reverse("imovel-situacao", args=[self.imovel.pk]), {
            "situacao": "ALUGADO", "observacao": "Locação fechada",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["situacao"], "ALUGADO")

        historico = self.client.get(reverse("imovel-historico", args=[self.imovel.pk])).data
        self.assertEqual(len(historico), 1)
        self.assertEqual(historico[0]["alterado_por"], "Corretor Teste")
        self.assertEqual(historico[0]["observacao"], "Locação fechada")

    def test_api_colega_recebe_403(self):
        self.client.force_authenticate(self.colega)
        response = self.client.post(reverse("imovel-situacao", args=[self.imovel.pk]), {
            "situacao": "VENDIDO",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(Imovel.objects.get(pk=self.imovel.pk).situacao, Imovel.Situacao.DISPONIVEL)

    def test_api_imovel_de_outra_empresa_da_404(self):
        self.client.force_authenticate(self.corretor)
        response = self.client.post(reverse("imovel-situacao", args=[self.de_fora.pk]), {
            "situacao": "VENDIDO",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_api_informa_se_pode_mudar_a_situacao(self):
        self.client.force_authenticate(self.corretor)
        dados = self.client.get(reverse("imovel-detail", args=[self.imovel.pk])).data
        self.assertTrue(dados["pode_mudar_situacao"])

        self.client.force_authenticate(self.colega)
        dados = self.client.get(reverse("imovel-detail", args=[self.imovel.pk])).data
        self.assertFalse(dados["pode_mudar_situacao"])

    def test_api_editar_o_imovel_continua_sem_mudar_a_situacao(self):
        self.client.force_authenticate(self.corretor)
        self.client.patch(reverse("imovel-detail", args=[self.imovel.pk]), {
            "situacao": "VENDIDO",
        }, format="json")
        self.assertEqual(Imovel.objects.get(pk=self.imovel.pk).situacao, Imovel.Situacao.DISPONIVEL)
        self.assertFalse(self.imovel.historico_situacao.exists())

    def test_api_corretor_continua_sem_publicar(self):
        self.client.force_authenticate(self.corretor)
        response = self.client.post(f"/api/imoveis/{self.imovel.pk}/publicar/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class AcervoApiTests(MediaTemporariaMixin, APITestCase):
    """Acervo do corretor (APP08): GET /api/imoveis/ paginado, filtrado e enxuto (D-06, D-07, D-10, D-13)."""

    CHAVES_ENVELOPE = {"count", "next", "previous", "results"}
    CHAVES_ITEM = {
        "id", "codigo", "titulo", "finalidade", "preco_venda", "preco_aluguel", "foto_capa",
        "situacao", "meu_imovel", "na_vitrine",
    }

    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.colega = criar_corretor(self.empresa, email="colega@teste.com")
        self.proprietario = criar_proprietario(self.empresa)

        self.outra_empresa = criar_empresa(nome="Outra", cnpj="19.771.776/0001-33")
        self.outro_corretor = criar_corretor(self.outra_empresa, email="outro@teste.com")
        self.outro_proprietario = criar_proprietario(self.outra_empresa, documento="10902516612")
        self.imovel_de_fora = criar_imovel(
            self.outra_empresa, self.outro_corretor, self.outro_proprietario, codigo="FORA"
        )

    def _criar(self, codigo, corretor=None, **extra):
        return criar_imovel(self.empresa, corretor or self.corretor, self.proprietario, codigo=codigo, **extra)

    def _lista(self, **parametros):
        self.client.force_authenticate(self.corretor)
        return self.client.get(reverse("imovel-list"), parametros)

    def _item(self, imovel):
        itens = {item["id"]: item for item in self._lista().data["results"]}
        return itens[imovel.pk]

    def test_ponta_a_ponta_com_token_real_devolve_o_envelope_paginado(self):
        self._criar("IM001")
        login = self.client.post(
            reverse("api-login"), {"email": "corretor@teste.com", "password": "senha-forte-123"}
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {login.data['token']}")

        response = self.client.get(reverse("imovel-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(set(response.data), self.CHAVES_ENVELOPE)
        self.assertEqual([item["codigo"] for item in response.data["results"]], ["IM001"])

    def test_pagina_de_20_com_next_absoluto(self):
        for numero in range(1, 26):
            self._criar(f"AC{numero:02d}")

        primeira = self._lista()
        self.assertEqual(primeira.data["count"], 25)
        self.assertEqual(len(primeira.data["results"]), 20)
        self.assertTrue(primeira.data["next"].startswith("http://testserver/"), primeira.data["next"])
        self.assertIn("page=2", primeira.data["next"])
        self.assertIsNone(primeira.data["previous"])

        segunda = self._lista(page=2)
        self.assertEqual(len(segunda.data["results"]), 5)
        self.assertIsNone(segunda.data["next"])

    def test_tamanho_da_pagina_nao_e_ajustavel_pelo_cliente(self):
        for numero in range(1, 26):
            self._criar(f"AC{numero:02d}")
        response = self._lista(page_size=3)
        self.assertEqual(len(response.data["results"]), 20)

    def test_item_tem_exatamente_as_10_chaves(self):
        self._criar("IM001")
        itens = self._lista().data["results"]
        self.assertEqual(len(itens), 1)
        self.assertEqual(set(itens[0]), self.CHAVES_ITEM)

    def test_meu_imovel_so_para_o_corretor_responsavel_e_os_dois_aparecem(self):
        meu = self._criar("MEU")
        do_colega = self._criar("COLEGA", corretor=self.colega)

        itens = {item["codigo"]: item for item in self._lista().data["results"]}

        self.assertEqual(set(itens), {"MEU", "COLEGA"})
        self.assertIs(itens["MEU"]["meu_imovel"], True)
        self.assertIs(itens["COLEGA"]["meu_imovel"], False)
        self.assertEqual(itens["MEU"]["id"], meu.pk)
        self.assertEqual(itens["COLEGA"]["id"], do_colega.pk)

    def test_na_vitrine_segue_a_regra_unica_publicado_disponivel_empresa_ativa(self):
        no_ar = self._criar("NOAR", publicado=True)
        rascunho = self._criar("RASC")
        reservado = self._criar("RESERV", publicado=True)
        Imovel.objects.filter(pk=reservado.pk).update(situacao=Imovel.Situacao.RESERVADO)

        self.assertIs(self._item(no_ar)["na_vitrine"], True)
        self.assertIs(self._item(rascunho)["na_vitrine"], False)
        self.assertIs(self._item(reservado)["na_vitrine"], False)

    def test_na_vitrine_falso_quando_a_empresa_esta_inativa(self):
        no_ar = self._criar("NOAR", publicado=True)
        Empresa.objects.filter(pk=self.empresa.pk).update(ativa=False)
        self.assertIs(self._item(no_ar)["na_vitrine"], False)

    def test_foto_capa_e_url_absoluta_da_capa_marcada_senao_a_primeira_senao_nula(self):
        sem_fotos = self._criar("SEMFOTO")
        so_primeira = self._criar("PRIMEIRA")
        primeira = FotoImovel.objects.create(imovel=so_primeira, imagem=gerar_imagem(), ordem=0)
        com_capa = self._criar("COMCAPA")
        FotoImovel.objects.create(imovel=com_capa, imagem=gerar_imagem(), ordem=0)
        capa = FotoImovel.objects.create(imovel=com_capa, imagem=gerar_imagem(), ordem=1)
        capa.definir_como_capa()

        self.assertIsNone(self._item(sem_fotos)["foto_capa"])
        self.assertEqual(
            self._item(so_primeira)["foto_capa"], f"http://testserver{primeira.imagem.url}"
        )
        self.assertEqual(self._item(com_capa)["foto_capa"], f"http://testserver{capa.imagem.url}")

    def test_ordem_e_do_mais_novo_para_o_mais_antigo(self):
        for codigo in ("A", "B", "C"):
            self._criar(codigo)
        codigos = [item["codigo"] for item in self._lista().data["results"]]
        self.assertEqual(codigos, ["C", "B", "A"])

    def test_detalhe_continua_completo_e_sem_envelope(self):
        imovel = self._criar("IM001")
        self.client.force_authenticate(self.corretor)
        response = self.client.get(reverse("imovel-detail", args=[imovel.pk]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("proprietario", response.data)
        self.assertNotIn("results", response.data)
        self.assertEqual(response.data["codigo"], "IM001")

    # ---- busca e situação pelo FiltroImovelForm do painel do site (D-08, D-09, D-11) ----

    def _codigos(self, **parametros):
        response = self._lista(**parametros)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return {item["codigo"] for item in response.data["results"]}

    def test_busca_por_codigo(self):
        for codigo in ("AC01", "AC02", "XY99"):
            self._criar(codigo)
        self.assertEqual(self._codigos(busca="AC0"), {"AC01", "AC02"})

    def test_busca_por_titulo(self):
        self._criar("IM001", titulo="Sobrado em Moema")
        self._criar("IM002", titulo="Casa bonita")
        self.assertEqual(self._codigos(busca="moema"), {"IM001"})

    def test_filtro_por_situacao(self):
        self._criar("DISP")
        reservado = self._criar("RESERV")
        vendido = self._criar("VEND")
        Imovel.objects.filter(pk=reservado.pk).update(situacao=Imovel.Situacao.RESERVADO)
        Imovel.objects.filter(pk=vendido.pk).update(situacao=Imovel.Situacao.VENDIDO)

        self.assertEqual(self._codigos(situacao="RESERVADO"), {"RESERV"})
        self.assertEqual(self._codigos(situacao="VENDIDO"), {"VEND"})
        self.assertEqual(self._codigos(situacao="DISPONIVEL"), {"DISP"})
        self.assertEqual(self._codigos(situacao="ALUGADO"), set())

    def test_busca_e_situacao_combinadas(self):
        self._criar("AC01")
        reservado = self._criar("AC02")
        outro_reservado = self._criar("ZZ03")
        Imovel.objects.filter(pk__in=[reservado.pk, outro_reservado.pk]).update(
            situacao=Imovel.Situacao.RESERVADO
        )
        self.assertEqual(self._codigos(busca="AC", situacao="RESERVADO"), {"AC02"})

    def test_situacao_invalida_nao_vira_erro_e_devolve_a_lista_sem_filtro(self):
        for codigo in ("AC01", "AC02", "XY99"):
            self._criar(codigo)
        # formulário inválido inteiro é ignorado (comportamento do site): nem a busca vale
        self.assertEqual(self._codigos(busca="AC01", situacao="XXX"), {"AC01", "AC02", "XY99"})

    def test_page_sozinho_nao_filtra_nada(self):
        for numero in range(1, 26):
            self._criar(f"AC{numero:02d}")
        response = self._lista(page=1)
        self.assertEqual(response.data["count"], 25)

    def test_busca_pagina_so_o_que_casou(self):
        for numero in range(1, 26):
            self._criar(f"AC{numero:02d}")
        self._criar("OUTRO")
        response = self._lista(busca="AC", page=2)
        self.assertEqual(response.data["count"], 25)
        self.assertEqual(len(response.data["results"]), 5)

    # ---- isolamento por empresa e perfis (D-14, AC-5) ----

    def test_isolamento_busca_situacao_e_parametros_forcados_nao_trazem_imovel_de_fora(self):
        self._criar("IM001")
        tentativas = (
            {"busca": "FORA"},
            {"situacao": "DISPONIVEL"},
            {"empresa": self.outra_empresa.pk},
            {"corretor": self.outro_corretor.pk},
            {"busca": "FORA", "situacao": "DISPONIVEL", "empresa": self.outra_empresa.pk},
        )
        for parametros in tentativas:
            with self.subTest(parametros=parametros):
                codigos = self._codigos(**parametros)
                self.assertNotIn("FORA", codigos)
        # os parâmetros de empresa/corretor são ignorados: o acervo da própria empresa segue inteiro
        self.assertEqual(self._codigos(empresa=self.outra_empresa.pk), {"IM001"})
        self.assertEqual(self._codigos(corretor=self.outro_corretor.pk), {"IM001"})

    def test_detalhe_de_imovel_de_outra_empresa_da_404(self):
        self.client.force_authenticate(self.corretor)
        response = self.client.get(reverse("imovel-detail", args=[self.imovel_de_fora.pk]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_gestor_recebe_403_na_lista(self):
        self.client.force_authenticate(criar_gestor(self.empresa))
        self.assertEqual(self.client.get(reverse("imovel-list")).status_code, status.HTTP_403_FORBIDDEN)

    def test_administrador_recebe_403_na_lista(self):
        administrador = Usuario.objects.create_user(
            email="admin@teste.com", senha="senha-forte-123", nome="Admin",
            perfil=Usuario.Perfil.ADMINISTRADOR,
        )
        self.client.force_authenticate(administrador)
        self.assertEqual(self.client.get(reverse("imovel-list")).status_code, status.HTTP_403_FORBIDDEN)

    def test_sem_credencial_recebe_401_na_lista(self):
        self.assertEqual(self.client.get(reverse("imovel-list")).status_code, status.HTTP_401_UNAUTHORIZED)

    # ---- custo fixo ----

    def test_numero_de_consultas_da_pagina_nao_cresce_com_o_numero_de_imoveis(self):
        def consultas_da_primeira_pagina():
            self.client.force_authenticate(self.corretor)
            with CaptureQueriesContext(connection) as contexto:
                response = self.client.get(reverse("imovel-list"))
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            return len(contexto.captured_queries)

        for numero in range(1, 4):
            imovel = self._criar(f"AC{numero:02d}", publicado=True)
            FotoImovel.objects.create(imovel=imovel, imagem=gerar_imagem(), ordem=0)
        com_3 = consultas_da_primeira_pagina()

        for numero in range(4, 26):
            imovel = self._criar(f"AC{numero:02d}", corretor=self.colega, publicado=True)
            FotoImovel.objects.create(imovel=imovel, imagem=gerar_imagem(), ordem=0)
        com_25 = consultas_da_primeira_pagina()

        self.assertEqual(com_3, com_25)


class AcervoAllowlistExataTests(MediaTemporariaMixin, APITestCase):
    """D-13/T-08-03: as chaves de cada item do acervo são travadas por igualdade exata + deny-list."""

    CHAVES_ITEM = {
        "id", "codigo", "titulo", "finalidade", "preco_venda", "preco_aluguel", "foto_capa",
        "situacao", "meu_imovel", "na_vitrine",
    }
    CHAVES_PROIBIDAS = (
        "proprietario", "corretor_responsavel", "empresa", "descricao", "fotos", "endereco",
        "publicado", "pode_publicar", "pendencias_para_publicar", "criado_em",
    )

    def setUp(self):
        self.empresa = criar_empresa()
        self.corretor = criar_corretor(self.empresa)
        self.proprietario = criar_proprietario(self.empresa)
        casa = criar_imovel(self.empresa, self.corretor, self.proprietario, codigo="CASA1", publicado=True)
        FotoImovel.objects.create(imovel=casa, imagem=gerar_imagem(), ordem=0)
        criar_imovel(
            self.empresa, self.corretor, self.proprietario, codigo="TERR1",
            natureza=Imovel.Natureza.TERRENO, finalidade=Imovel.Finalidade.ALUGUEL,
            preco_venda=None, preco_aluguel="1500.00", descricao="Texto interno do corretor.",
        )

    def test_todo_item_da_lista_tem_as_chaves_exatas_e_nenhuma_proibida(self):
        self.client.force_authenticate(self.corretor)
        itens = self.client.get(reverse("imovel-list")).json()["results"]
        self.assertEqual({item["codigo"] for item in itens}, {"CASA1", "TERR1"})
        for item in itens:
            with self.subTest(codigo=item["codigo"]):
                self.assertEqual(set(item), self.CHAVES_ITEM)
                for chave in self.CHAVES_PROIBIDAS:
                    self.assertNotIn(chave, item)
