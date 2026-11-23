from datetime import datetime
from unittest import mock
from urllib.parse import parse_qs, unquote, urlsplit

from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from contas.models import Usuario
from contas.tests import fixar_taxa_alta_no_login
from imoveis.tests import (
    VitrineBase, criar_corretor, criar_empresa, criar_endereco, criar_gestor, criar_imovel,
    criar_proprietario,
)

from .api.views import PaginacaoContatos
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
        return {item["id"] for item in response.data["results"]}

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


class PaginacaoDaCaixaTests(ContatoBase):
    """D-12: a lista do app vem em envelope paginado, 20 por página, mais recentes primeiro."""

    CHAVES_DO_ITEM = {
        "id", "imovel", "imovel_codigo", "imovel_titulo", "corretor", "corretor_nome", "canal",
        "canal_exibicao", "criado_em", "retorno", "retorno_exibicao", "retorno_em",
    }

    def setUp(self):
        super().setUp()
        canais = [Contato.Canal.WHATSAPP, Contato.Canal.TELEFONE, Contato.Canal.EMAIL]
        for numero in range(25):
            Contato.objects.create(
                empresa_id=self.empresa.pk, imovel=self.casa, corretor=self.corretor,
                canal=canais[numero % 3],
            )

    def test_ponta_a_ponta_com_login_real_e_token(self):
        fixar_taxa_alta_no_login(self)
        login = self.client.post(
            reverse("api-login"), {"email": self.corretor.email, "password": "senha-forte-123"}
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {login.data['token']}")
        response = self.client.get(reverse("contato-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(set(response.data), {"count", "next", "previous", "results"})
        self.assertEqual(response.data["count"], 25)
        self.assertEqual(len(response.data["results"]), 20)

    def test_primeira_e_segunda_pagina(self):
        self.client.force_login(self.corretor)
        url = reverse("contato-list")
        primeira = self.client.get(url)
        self.assertEqual(len(primeira.data["results"]), 20)
        self.assertEqual(primeira.data["count"], 25)
        self.assertTrue(primeira.data["next"].startswith("http://testserver/"))
        self.assertIn("page=2", primeira.data["next"])
        self.assertIsNone(primeira.data["previous"])
        segunda = self.client.get(url, {"page": 2})
        self.assertEqual(len(segunda.data["results"]), 5)
        self.assertIsNone(segunda.data["next"])
        self.assertIsNotNone(segunda.data["previous"])

    def test_todo_item_tem_as_12_chaves_do_serializer(self):
        self.client.force_login(self.corretor)
        for item in self.client.get(reverse("contato-list")).data["results"]:
            self.assertEqual(set(item), self.CHAVES_DO_ITEM)

    def test_ordem_das_duas_paginas_e_a_do_servidor(self):
        self.client.force_login(self.corretor)
        url = reverse("contato-list")
        ids = [item["id"] for item in self.client.get(url).data["results"]]
        ids += [item["id"] for item in self.client.get(url, {"page": 2}).data["results"]]
        esperado = list(
            Contato.objects.filter(corretor=self.corretor).order_by("-criado_em", "-id").values_list("id", flat=True)
        )
        self.assertEqual(ids, esperado)

    def test_next_preserva_os_filtros(self):
        self.client.force_login(self.corretor)
        with mock.patch.object(PaginacaoContatos, "page_size", 1):
            response = self.client.get(reverse("contato-list"), {"de": "2020-01-01", "ate": "2100-12-31"})
        proximo = response.data["next"]
        self.assertIn("de=2020-01-01", proximo)
        self.assertIn("ate=2100-12-31", proximo)
        self.assertIn("page=2", proximo)

    def test_pagina_inexistente_da_404_e_tamanho_do_cliente_e_ignorado(self):
        self.client.force_login(self.corretor)
        url = reverse("contato-list")
        self.assertEqual(self.client.get(url, {"page": 99}).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(len(self.client.get(url, {"page_size": 50}).data["results"]), 20)

    def test_detalhe_e_patch_devolvem_o_item_sem_envelope(self):
        self.client.force_login(self.corretor)
        contato = Contato.objects.filter(corretor=self.corretor).first()
        url = reverse("contato-detail", args=[contato.pk])
        detalhe = self.client.get(url)
        self.assertEqual(detalhe.data["id"], contato.pk)
        self.assertNotIn("results", detalhe.data)
        anotado = self.client.patch(url, {"retorno": "FALEI"}, format="json")
        self.assertEqual(anotado.status_code, status.HTTP_200_OK)
        self.assertEqual(anotado.data["id"], contato.pk)
        self.assertNotIn("results", anotado.data)


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
