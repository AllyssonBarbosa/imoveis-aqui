"""Regras do contato (W11): fica tudo aqui, no Django — o site e a API só chamam.

- registrar_contato(): o servidor grava o contato e devolve o link pronto
  (WhatsApp, telefone ou e-mail) com a mensagem padrão.
- contatos_visiveis_para(): quem enxerga qual contato (critério 6).
- filtrar_contatos(): filtros da caixa de contatos (imóvel e período).

Sobre o código do imóvel: é o código interno da empresa e, pela regra da
W09, não aparece na vitrine (nem no HTML do site, nem no JSON da API).
Ele entra só na mensagem montada aqui, na hora do clique, para o corretor
reconhecer de qual imóvel se trata.
"""

from datetime import date
from urllib.parse import quote, urlencode

from contas.models import Usuario
from core.validators import somente_digitos
from imoveis.vitrine import FiltroInvalido

from .models import Contato

MENSAGEM_PADRAO = (
    'Olá! Vi no Imóveis Aqui o imóvel "{titulo}" (código {codigo}) '
    "e gostaria de mais informações. {url}"
)


class CanalIndisponivel(Exception):
    """O destino não tem número/e-mail para o canal pedido — nada é gravado."""


def _com_codigo_do_brasil(numero):
    """Número sem o 55 (DDD + número, até 11 dígitos) ganha o código do Brasil."""
    digitos = somente_digitos(numero or "")
    if not digitos:
        return None
    if len(digitos) <= 11:
        digitos = f"55{digitos}"
    return digitos


def link_whatsapp(numero, mensagem):
    digitos = _com_codigo_do_brasil(numero)
    if not digitos:
        return None
    return f"https://wa.me/{digitos}?{urlencode({'text': mensagem})}"


def link_telefone(numero):
    digitos = _com_codigo_do_brasil(numero)
    return f"tel:+{digitos}" if digitos else None


def link_email(email, assunto, corpo):
    if not email:
        return None
    # mailto usa %20 para espaço (o "+" do urlencode padrão apareceria no texto)
    return f"mailto:{email}?{urlencode({'subject': assunto, 'body': corpo}, quote_via=quote)}"


def destino_do_contato(imovel):
    """Para quem vai o contato: (corretor, dados) ou (None, dados da empresa).

    Mesma regra do botão da W10: corretor desativado não recebe contato,
    então quem atende é a empresa dona do imóvel. Corretor autônomo que
    não preencheu telefone/WhatsApp usa os da empresa (dados_perfil_publico).
    """
    corretor = imovel.corretor_responsavel
    if corretor.is_active:
        return corretor, corretor.dados_perfil_publico()
    empresa = imovel.empresa
    return None, {"whatsapp": empresa.whatsapp, "telefone": empresa.telefone, "email": empresa.email}


def _links(imovel, url_do_anuncio):
    """Link de cada canal para este imóvel (None quando não há número/e-mail)."""
    _, dados = destino_do_contato(imovel)
    mensagem = MENSAGEM_PADRAO.format(titulo=imovel.titulo, codigo=imovel.codigo, url=url_do_anuncio)
    return {
        Contato.Canal.WHATSAPP: link_whatsapp(dados["whatsapp"], mensagem),
        Contato.Canal.TELEFONE: link_telefone(dados["telefone"]),
        Contato.Canal.EMAIL: link_email(
            dados["email"], f"Imóvel {imovel.codigo} — {imovel.titulo}", mensagem
        ),
    }


def canais_disponiveis(imovel):
    """Canais que têm destino — o site só mostra o botão desses.

    Devolve só os nomes dos canais, nunca o link: o link (com o código
    interno) só sai do servidor no momento do clique.
    """
    return [canal for canal, link in _links(imovel, "").items() if link]


def registrar_contato(imovel, canal, url_do_anuncio):
    """Grava o contato e devolve (contato, link). Sem destino, levanta CanalIndisponivel."""
    if canal not in Contato.Canal.values:
        raise CanalIndisponivel("Canal inválido. Opções: WHATSAPP, TELEFONE, EMAIL.")
    link = _links(imovel, url_do_anuncio)[canal]
    if not link:
        raise CanalIndisponivel("Este imóvel não tem contato disponível por esse canal.")
    corretor, _ = destino_do_contato(imovel)
    contato = Contato.objects.create(
        empresa_id=imovel.empresa_id, imovel=imovel, corretor=corretor, canal=canal
    )
    return contato, link


def restringir_ao_perfil(queryset, usuario):
    """Recebe contatos JÁ filtrados pela empresa e corta pelo perfil.

    - gestor: todos os contatos da empresa
    - corretor: só os que ele recebeu
    - qualquer outro (administrador): nada
    """
    if usuario.perfil == Usuario.Perfil.GESTOR:
        return queryset
    if usuario.perfil == Usuario.Perfil.CORRETOR:
        return queryset.filter(corretor=usuario)
    return queryset.none()


def contatos_visiveis_para(usuario):
    """Critério 6: sempre filtra pela empresa do usuário e depois pelo perfil."""
    if not usuario.is_authenticated or usuario.empresa_id is None:
        return Contato.objects.none()
    return restringir_ao_perfil(Contato.objects.filter(empresa_id=usuario.empresa_id), usuario)


def _data(params, nome):
    valor = (params.get(nome) or "").strip()
    if not valor:
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        raise FiltroInvalido({nome: "Informe a data no formato AAAA-MM-DD."})


def filtrar_contatos(queryset, params):
    """Filtros da caixa: ?imovel=<id>&de=AAAA-MM-DD&ate=AAAA-MM-DD (datas inclusivas)."""
    imovel = (params.get("imovel") or "").strip()
    if imovel:
        if not imovel.isdigit():
            raise FiltroInvalido({"imovel": "Informe o id do imóvel."})
        queryset = queryset.filter(imovel_id=int(imovel))

    de, ate = _data(params, "de"), _data(params, "ate")
    if de and ate and de > ate:
        raise FiltroInvalido({"de": "A data inicial não pode ser depois da final."})
    if de:
        queryset = queryset.filter(criado_em__date__gte=de)
    if ate:
        queryset = queryset.filter(criado_em__date__lte=ate)
    return queryset
