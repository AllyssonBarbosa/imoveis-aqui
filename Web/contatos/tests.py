from datetime import datetime
from unittest import mock
from urllib.parse import parse_qs, unquote, urlsplit

from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny

from contas.models import Usuario
from core.throttles import ThrottleContatoPublico, ThrottleVitrinePublica
from imoveis.models import Imovel
from imoveis.tests import (
    TAXA_ALTA_NOS_TESTES, VitrineBase, criar_corretor, criar_empresa, criar_endereco, criar_gestor,
    criar_imovel, criar_proprietario,
)

from .api.views import ContatoPublicoAPIView
from .servicos import link_whatsapp

from .models import Contato


class ContatoBase(VitrineBase):
    """Cenário da vitrine (W09) mais o que o critério 6 precisa:

    - empresa A (self.empresa): corretor 1 (self.corretor, dono da casa), corretor 2
      (dono de outra casa) e o gestor;
    - empresa B (self.outra): um corretor, um gestor e um imóvel publicado;
    - o administrador, que não pertence a empresa nenhuma.
    """

    def setUp(self):
        super().setUp()
        self.gestor = criar_gestor(self.empresa)
        self.corretor2 = criar_corretor(self.empresa, email="corretor2@teste.com")
        self.casa2 = criar_imovel(
            self.empresa, self.corretor2, self.proprietario, codigo="DO-CORRETOR-2",
            titulo="Casa do segundo corretor", endereco=criar_endereco(self.sp, "Moema"), publicado=True,
        )

        self.outra = criar_empresa(nome="Outra Imobiliária", cnpj="11.444.777/0001-61")
        self.corretor_b = criar_corretor(self.outra, email="corretor@outra.com")
        self.gestor_b = criar_gestor(self.outra, email="gestor@outra.com")
        self.imovel_b = criar_imovel(
            self.outra, self.corretor_b, criar_proprietario(self.outra), codigo="OUTRA-1",
            titulo="Casa da outra", endereco=criar_endereco(self.sp, "Centro"), publicado=True,
        )

        self.admin = Usuario.objects.create_superuser(
            email="admin@teste.com", senha="senha-forte-123", nome="Admin"
        )

    # --- atalhos ---

    def contatar_site(self, imovel, canal="WHATSAPP"):
        return self.client.post(reverse("site_publico:contato", args=[imovel.pk]), {"canal": canal})

    def contatar_api(self, imovel, canal="WHATSAPP", **extra):
        url = reverse("publico-imovel-contato", args=[imovel.pk])
        return self.client.post(url, {"canal": canal}, format="json", **extra)

    def caixa(self, usuario, **filtros):
        self.client.force_login(usuario)
        return self.client.get(reverse("contatos:contato_list"), filtros)

    def ids_caixa(self, usuario, **filtros):
        response = self.caixa(usuario, **filtros)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return {contato.pk for contato in response.context["contatos"]}

    def ids_api(self, usuario, **filtros):
        self.client.force_login(usuario)
        response = self.client.get(reverse("contato-list"), filtros)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return {item["id"] for item in response.data}

    def anotar(self, usuario, contato, retorno):
        self.client.force_login(usuario)
        return self.client.post(
            reverse("contatos:contato_anotar_retorno", args=[contato.pk]), {"retorno": retorno}
        )

    def novo_contato(self, imovel, canal="WHATSAPP"):
        self.contatar_api(imovel, canal)
        return Contato.objects.latest("id")


class LinkDoWhatsAppTests(ContatoBase):
    """Critério 1: o servidor monta o link do WhatsApp com código e título do imóvel."""

    def mensagem(self, link):
        return parse_qs(urlsplit(link).query)["text"][0]

    def test_site_redireciona_para_o_whatsapp_com_codigo_e_titulo(self):
        response = self.contatar_site(self.casa)
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        link = response["Location"]
        self.assertTrue(link.startswith("https://wa.me/5511988887777?text="))
        mensagem = self.mensagem(link)
        self.assertIn("CASA-INTERNO-1", mensagem)
        self.assertIn("Casa com quintal", mensagem)
        self.assertIn(f"/imovel/{self.casa.pk}/", mensagem)

    def test_api_devolve_o_link_pronto_para_o_app(self):
        response = self.contatar_api(self.casa)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mensagem = self.mensagem(response.data["link"])
        self.assertIn("CASA-INTERNO-1", mensagem)
        self.assertIn("Casa com quintal", mensagem)

    def test_codigo_interno_continua_fora_da_pagina_e_da_api_da_vitrine(self):
        # regra da W09: o código só sai do servidor dentro do link, na hora do clique
        pagina = self.client.get(reverse("site_publico:imovel", args=[self.casa.pk]))
        self.assertNotContains(pagina, "CASA-INTERNO-1")
        self.assertNotContains(pagina, "wa.me")
        api = self.client.get(reverse("publico-imovel", args=[self.casa.pk]))
        self.assertNotIn("CASA-INTERNO-1", api.content.decode())


class GravacaoDoContatoTests(ContatoBase):
    """Critério 2: cada contato é gravado com imóvel, corretor, canal e data."""

    def test_grava_imovel_corretor_canal_e_data(self):
        antes = timezone.now()
        self.contatar_site(self.casa)
        contato = Contato.objects.get()
        self.assertEqual(contato.imovel, self.casa)
        self.assertEqual(contato.corretor, self.corretor)
        self.assertEqual(contato.empresa, self.empresa)
        self.assertEqual(contato.canal, Contato.Canal.WHATSAPP)
        self.assertGreaterEqual(contato.criado_em, antes)
        self.assertEqual(contato.retorno, Contato.Retorno.PENDENTE)

    def test_telefone_grava_e_redireciona_para_a_ligacao(self):
        response = self.contatar_site(self.casa, "TELEFONE")
        self.assertEqual(response["Location"], "tel:+5511988887777")
        self.assertEqual(Contato.objects.get().canal, Contato.Canal.TELEFONE)

    def test_email_grava_e_abre_o_email_com_codigo_e_titulo(self):
        link = self.contatar_site(self.casa, "EMAIL")["Location"]
        self.assertTrue(link.startswith("mailto:corretor@teste.com?"))
        self.assertIn("CASA-INTERNO-1", unquote(link))
        self.assertIn("Casa com quintal", unquote(link))
        self.assertEqual(Contato.objects.get().canal, Contato.Canal.EMAIL)

    def test_corretor_desativado_o_contato_vai_para_a_empresa(self):
        self.corretor.is_active = False
        self.corretor.save(update_fields=["is_active"])
        response = self.contatar_site(self.casa)
        self.assertTrue(response["Location"].startswith("https://wa.me/5511999999999?"))
        contato = Contato.objects.get()
        self.assertIsNone(contato.corretor)
        self.assertEqual(contato.empresa, self.empresa)

    def test_get_nao_grava_contato(self):
        # robôs e pré-carregamento do navegador fazem GET — isso não pode virar contato
        response = self.client.get(reverse("site_publico:contato", args=[self.casa.pk]), {"canal": "WHATSAPP"})
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        response = self.client.get(reverse("publico-imovel-contato", args=[self.casa.pk]))
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertFalse(Contato.objects.exists())

    def test_canal_invalido_e_recusado_sem_gravar(self):
        self.assertEqual(self.contatar_site(self.casa, "SINAL_DE_FUMACA").status_code, 400)
        self.assertEqual(self.contatar_api(self.casa, "SINAL_DE_FUMACA").status_code, 400)
        self.assertFalse(Contato.objects.exists())

    def test_canal_sem_destino_e_recusado_sem_gravar(self):
        # corretor desativado e empresa sem telefone: não há para onde ligar
        self.corretor.is_active = False
        self.corretor.save(update_fields=["is_active"])
        self.assertEqual(self.contatar_api(self.casa, "TELEFONE").status_code, 400)
        self.assertFalse(Contato.objects.exists())
        pagina = self.client.get(reverse("site_publico:imovel", args=[self.casa.pk]))
        self.assertNotContains(pagina, 'value="TELEFONE"')

    def test_nenhum_dado_do_visitante_e_guardado(self):
        self.client.post(
            reverse("publico-imovel-contato", args=[self.casa.pk]),
            {"canal": "WHATSAPP", "nome": "Visitante", "telefone": "11911112222"}, format="json",
        )
        campos = {campo.name for campo in Contato._meta.get_fields()}
        self.assertEqual(
            campos, {"id", "empresa", "imovel", "corretor", "canal", "criado_em", "retorno", "retorno_em"}
        )


class SemLoginTests(ContatoBase):
    """Critério 3: o registro é por endereço aberto, sem login do visitante."""

    def test_site_e_api_gravam_sem_login(self):
        self.assertEqual(self.contatar_site(self.casa).status_code, status.HTTP_302_FOUND)
        self.assertEqual(self.contatar_api(self.casa).status_code, status.HTTP_201_CREATED)
        self.assertEqual(Contato.objects.count(), 2)

    def test_token_invalido_do_app_nao_atrapalha(self):
        response = self.contatar_api(self.casa, HTTP_AUTHORIZATION="Token vencido")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_rascunho_e_empresa_inativa_nao_recebem_contato(self):
        for imovel in (self.rascunho, self.da_inativa):
            with self.subTest(imovel=imovel.codigo):
                self.assertEqual(self.contatar_site(imovel).status_code, status.HTTP_404_NOT_FOUND)
                self.assertEqual(self.contatar_api(imovel).status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(Contato.objects.exists())


class CaixaDeContatosTests(ContatoBase):
    """Critério 4: caixa de contatos do corretor, com filtro por imóvel e por período."""

    def setUp(self):
        super().setUp()
        self.da_casa = self.novo_contato(self.casa)
        self.do_sobrado = self.novo_contato(self.sobrado, "EMAIL")
        self.antigo = self.novo_contato(self.casa, "TELEFONE")
        Contato.objects.filter(pk=self.antigo.pk).update(
            criado_em=timezone.make_aware(datetime(2026, 1, 10, 15, 0))
        )

    def test_caixa_lista_os_contatos_do_corretor(self):
        self.assertEqual(self.ids_caixa(self.corretor), {self.da_casa.pk, self.do_sobrado.pk, self.antigo.pk})
        response = self.caixa(self.corretor)
        self.assertContains(response, "Casa com quintal")
        self.assertContains(response, "CASA-INTERNO-1")  # no painel o código interno aparece

    def test_filtro_por_imovel(self):
        self.assertEqual(self.ids_caixa(self.corretor, imovel=self.casa.pk), {self.da_casa.pk, self.antigo.pk})
        self.assertEqual(self.ids_api(self.corretor, imovel=self.sobrado.pk), {self.do_sobrado.pk})

    def test_filtro_por_periodo_com_datas_inclusivas(self):
        self.assertEqual(self.ids_caixa(self.corretor, de="2026-01-10", ate="2026-01-10"), {self.antigo.pk})
        self.assertEqual(self.ids_caixa(self.corretor, ate="2026-01-31"), {self.antigo.pk})
        self.assertEqual(self.ids_api(self.corretor, de="2026-02-01"), {self.da_casa.pk, self.do_sobrado.pk})

    def test_filtro_combinado_imovel_e_periodo(self):
        self.assertEqual(
            self.ids_caixa(self.corretor, imovel=self.casa.pk, de="2026-02-01"), {self.da_casa.pk}
        )

    def test_filtro_invalido_mostra_mensagem_sem_quebrar(self):
        response = self.caixa(self.corretor, de="10/01/2026")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, "AAAA-MM-DD")
        self.client.force_login(self.corretor)
        api = self.client.get(reverse("contato-list"), {"de": "2026-02-10", "ate": "2026-02-01"})
        self.assertEqual(api.status_code, status.HTTP_400_BAD_REQUEST)

    def test_painel_home_tem_o_link_da_caixa(self):
        for usuario in (self.corretor, self.gestor):
            self.client.force_login(usuario)
            response = self.client.get(reverse("contas:painel"))
            self.assertContains(response, reverse("contatos:contato_list"))


class AnotarRetornoTests(ContatoBase):
    """Critério 5: o corretor anota o retorno — falei, visita agendada, sem resposta, descartado."""

    def setUp(self):
        super().setUp()
        self.contato = self.novo_contato(self.casa)

    def test_corretor_anota_cada_retorno(self):
        for retorno in Contato.RETORNOS_ANOTAVEIS:
            with self.subTest(retorno=retorno):
                response = self.anotar(self.corretor, self.contato, retorno)
                self.assertEqual(response.status_code, status.HTTP_302_FOUND)
                self.contato.refresh_from_db()
                self.assertEqual(self.contato.retorno, retorno)
                self.assertIsNotNone(self.contato.retorno_em)

    def test_gestor_da_mesma_empresa_tambem_anota(self):
        self.anotar(self.gestor, self.contato, "VISITA_AGENDADA")
        self.contato.refresh_from_db()
        self.assertEqual(self.contato.retorno, Contato.Retorno.VISITA_AGENDADA)

    def test_retorno_invalido_e_recusado(self):
        for valor in ("PENDENTE", "QUALQUER", ""):
            with self.subTest(valor=valor):
                self.anotar(self.corretor, self.contato, valor)
                self.contato.refresh_from_db()
                self.assertEqual(self.contato.retorno, Contato.Retorno.PENDENTE)

    def test_volta_para_a_caixa_com_os_mesmos_filtros(self):
        self.client.force_login(self.corretor)
        response = self.client.post(
            reverse("contatos:contato_anotar_retorno", args=[self.contato.pk]),
            {"retorno": "FALEI", "filtros": f"imovel={self.casa.pk}&de=2026-01-01"},
        )
        self.assertEqual(
            response["Location"], f"{reverse('contatos:contato_list')}?imovel={self.casa.pk}&de=2026-01-01"
        )

    def test_api_anota_o_retorno_e_nao_deixa_mudar_o_resto(self):
        self.client.force_login(self.corretor)
        url = reverse("contato-detail", args=[self.contato.pk])
        response = self.client.patch(
            url, {"retorno": "SEM_RESPOSTA", "canal": "EMAIL", "imovel": self.sobrado.pk}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.contato.refresh_from_db()
        self.assertEqual(self.contato.retorno, Contato.Retorno.SEM_RESPOSTA)
        self.assertEqual(self.contato.canal, Contato.Canal.WHATSAPP)
        self.assertEqual(self.contato.imovel, self.casa)
        self.assertEqual(self.client.patch(url, {"retorno": "PENDENTE"}, format="json").status_code, 400)

    def test_api_nao_cria_nem_apaga_contato(self):
        self.client.force_login(self.corretor)
        self.assertEqual(
            self.client.post(reverse("contato-list"), {"imovel": self.casa.pk, "canal": "EMAIL"}).status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.delete(reverse("contato-detail", args=[self.contato.pk])).status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )


class IsolamentoTests(ContatoBase):
    """Critério 6: o contato aparece para o corretor do imóvel e para o gestor da empresa,
    nunca para outro corretor nem para outra empresa (no painel e na API)."""

    def setUp(self):
        super().setUp()
        self.do_corretor1 = self.novo_contato(self.casa)
        self.do_corretor2 = self.novo_contato(self.casa2)
        self.da_outra = self.novo_contato(self.imovel_b)
        self.corretor.is_active = False  # contato que foi para a empresa A
        self.corretor.save(update_fields=["is_active"])
        self.da_empresa = self.novo_contato(self.sobrado)
        self.corretor.is_active = True
        self.corretor.save(update_fields=["is_active"])
        self.da_empresa_a = {self.do_corretor1.pk, self.do_corretor2.pk, self.da_empresa.pk}

    def test_corretor_ve_so_os_proprios_contatos(self):
        self.assertEqual(self.ids_caixa(self.corretor), {self.do_corretor1.pk})
        self.assertEqual(self.ids_api(self.corretor), {self.do_corretor1.pk})
        self.assertEqual(self.ids_caixa(self.corretor2), {self.do_corretor2.pk})
        self.assertEqual(self.ids_api(self.corretor2), {self.do_corretor2.pk})

    def test_gestor_ve_todos_os_da_propria_empresa(self):
        self.assertEqual(self.ids_caixa(self.gestor), self.da_empresa_a)
        self.assertEqual(self.ids_api(self.gestor), self.da_empresa_a)
        self.assertContains(self.caixa(self.gestor), "Empresa (corretor desativado)")

    def test_outra_empresa_nao_ve_nada_da_empresa_a(self):
        for usuario in (self.corretor_b, self.gestor_b):
            with self.subTest(usuario=usuario.email):
                self.assertEqual(self.ids_caixa(usuario), {self.da_outra.pk})
                self.assertEqual(self.ids_api(usuario), {self.da_outra.pk})

    def test_trocar_o_id_na_url_cai_em_404(self):
        # de outro corretor da mesma empresa e de outra empresa: some, sem revelar que existe
        casos = [
            (self.corretor, self.do_corretor2), (self.corretor, self.da_outra),
            (self.corretor, self.da_empresa), (self.gestor, self.da_outra),
            (self.corretor_b, self.do_corretor1), (self.gestor_b, self.do_corretor2),
        ]
        for usuario, contato in casos:
            with self.subTest(usuario=usuario.email, contato=contato.pk):
                self.client.force_login(usuario)
                url = reverse("contato-detail", args=[contato.pk])
                self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)
                self.assertEqual(
                    self.client.patch(url, {"retorno": "DESCARTADO"}, format="json").status_code,
                    status.HTTP_404_NOT_FOUND,
                )
                self.assertEqual(
                    self.anotar(usuario, contato, "DESCARTADO").status_code, status.HTTP_404_NOT_FOUND
                )
                contato.refresh_from_db()
                self.assertEqual(contato.retorno, Contato.Retorno.PENDENTE)

    def test_filtro_por_imovel_de_outro_nao_abre_nada(self):
        self.assertEqual(self.ids_caixa(self.corretor, imovel=self.casa2.pk), set())
        self.assertEqual(self.ids_api(self.gestor_b, imovel=self.casa.pk), set())

    def test_lista_de_imoveis_do_filtro_so_tem_os_visiveis(self):
        imoveis = set(self.caixa(self.corretor).context["imoveis"])
        self.assertEqual(imoveis, {self.casa})
        imoveis_gestor = set(self.caixa(self.gestor).context["imoveis"])
        self.assertEqual(imoveis_gestor, {self.casa, self.casa2, self.sobrado})
        self.assertNotContains(self.caixa(self.gestor), "Casa da outra")

    def test_administrador_nao_ve_contatos(self):
        self.assertEqual(self.caixa(self.admin).status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("contato-list")).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            self.anotar(self.admin, self.do_corretor1, "FALEI").status_code, status.HTTP_403_FORBIDDEN
        )

    def test_sem_login_nao_acessa_a_caixa(self):
        response = self.client.get(reverse("contatos:contato_list"))
        self.assertRedirects(response, f"{reverse('contas:login')}?next={reverse('contatos:contato_list')}")
        self.assertIn(
            self.client.get(reverse("contato-list")).status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )


class ContatoSituacaoTests(ContatoBase):
    """APP06: o POST público da W11 roda sobre a regra de vitrine (na_vitrine) da main."""

    def test_post_publico_sem_token_grava_e_devolve_canal_e_link(self):
        response = self.contatar_api(self.casa, "TELEFONE")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(set(response.data), {"canal", "link"})
        self.assertTrue(response.data["link"].startswith("tel:+55"))
        contato = Contato.objects.latest("id")
        self.assertEqual(contato.imovel, self.casa)
        self.assertEqual(contato.corretor, self.corretor)
        self.assertEqual(contato.canal, Contato.Canal.TELEFONE)

    def test_imovel_reservado_nao_recebe_contato(self):
        self.casa.mudar_situacao(Imovel.Situacao.RESERVADO, self.gestor)
        antes = Contato.objects.count()
        response = self.contatar_api(self.casa)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(Contato.objects.count(), antes)


class ContatoLimiteDeTaxaTests(ContatoBase):
    """APP06: o POST público de contato grava na caixa do corretor, então tem balde e taxa próprios."""

    def test_passou_da_taxa_recebe_429_sem_gravar(self):
        antes = Contato.objects.count()
        with mock.patch.object(ThrottleContatoPublico, "rate", "2/min", create=True):
            primeira = self.contatar_api(self.casa)
            segunda = self.contatar_api(self.casa)
            terceira = self.contatar_api(self.casa)
        self.assertEqual(primeira.status_code, status.HTTP_201_CREATED)
        self.assertEqual(segunda.status_code, status.HTTP_201_CREATED)
        self.assertEqual(terceira.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn("Retry-After", terceira)
        self.assertIn("detail", terceira.data)
        self.assertEqual(Contato.objects.count(), antes + 2)

    def test_view_usa_o_throttle_de_contato_sem_login(self):
        self.assertEqual(ContatoPublicoAPIView.throttle_classes, [ThrottleContatoPublico])
        self.assertEqual(ThrottleContatoPublico.scope, "contato_publico")
        self.assertEqual(ContatoPublicoAPIView.authentication_classes, [])
        self.assertEqual(ContatoPublicoAPIView.permission_classes, [AllowAny])

    def test_contato_herda_a_taxa_alta_da_suite(self):
        # sem patch próprio, a subclasse enxerga a taxa fixada na ThrottleVitrinePublica pelo setUp
        self.assertEqual(ThrottleContatoPublico().rate, TAXA_ALTA_NOS_TESTES)

    def test_cota_de_contato_esgotada_nao_bloqueia_a_vitrine(self):
        with mock.patch.object(ThrottleContatoPublico, "rate", "1/min", create=True):
            self.assertEqual(self.contatar_api(self.casa).status_code, status.HTTP_201_CREATED)
            self.assertEqual(self.contatar_api(self.casa).status_code, status.HTTP_429_TOO_MANY_REQUESTS)
            detalhe = self.client.get(reverse("publico-imovel", args=[self.casa.pk]))
        self.assertEqual(detalhe.status_code, status.HTTP_200_OK)

    def test_vitrine_esgotada_nao_bloqueia_o_contato(self):
        # as duas taxas explícitas: a subclasse herdaria a da vitrine se só a do pai fosse trocada
        with mock.patch.object(ThrottleVitrinePublica, "rate", "1/min", create=True), mock.patch.object(
            ThrottleContatoPublico, "rate", "100000/min", create=True
        ):
            url = reverse("publico-imovel", args=[self.casa.pk])
            self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)
            self.assertEqual(self.client.get(url).status_code, status.HTTP_429_TOO_MANY_REQUESTS)
            contato = self.contatar_api(self.casa)
        self.assertEqual(contato.status_code, status.HTTP_201_CREATED)

    def test_taxa_padrao_vem_do_settings(self):
        # `get_rate()` lê o settings e ignora o patch de taxa alta do setUp (que troca só `.rate`)
        esperada = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["contato_publico"]
        self.assertEqual(ThrottleContatoPublico().get_rate(), esperada)
        self.assertEqual(esperada, settings.CONTATO_TAXA_ANONIMA)

    def test_x_forwarded_for_forjado_nao_reinicia_a_contagem(self):
        configuracao = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 0}
        with override_settings(REST_FRAMEWORK=configuracao), mock.patch.object(
            ThrottleContatoPublico, "rate", "2/min", create=True
        ):
            respostas = [
                self.contatar_api(self.casa, HTTP_X_FORWARDED_FOR=f"10.0.0.{n}").status_code
                for n in (1, 2, 3)
            ]
        self.assertEqual(
            respostas,
            [status.HTTP_201_CREATED, status.HTTP_201_CREATED, status.HTTP_429_TOO_MANY_REQUESTS],
        )


class MensagemDoWhatsAppComEspacosTests(ContatoBase):
    """APP06 (Pitfall 7): o texto do link do WhatsApp usa %20 para espaço, como o mailto.

    Um cliente do WhatsApp que não aplica a decodificação de formulário mostraria "+" entre
    as palavras. A correção fica no servidor (decisão do usuário após a checagem do plano):
    o app abre o link como recebe, sem recodificar.
    """

    def test_api_usa_por_cento_20_e_nunca_mais(self):
        response = self.contatar_api(self.casa)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        link = response.data["link"]
        self.assertTrue(link.startswith("https://wa.me/"))
        self.assertIn("%20", link)
        self.assertNotIn("+", link)
        # unquote não troca "+" por espaço: se as palavras chegam separadas, o %20 está certo
        texto = unquote(urlsplit(link).query)
        self.assertIn("Casa com quintal", texto)
        self.assertIn("CASA-INTERNO-1", texto)

    def test_site_usa_por_cento_20_e_nunca_mais(self):
        response = self.contatar_site(self.casa)
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        link = response["Location"]
        self.assertIn("%20", link)
        self.assertNotIn("+", link)
        self.assertIn("Casa com quintal", unquote(urlsplit(link).query))

    def test_mais_literal_da_mensagem_vira_2b(self):
        self.assertEqual(
            link_whatsapp("11988887777", "a b+c"),
            "https://wa.me/5511988887777?text=a%20b%2Bc",
        )
