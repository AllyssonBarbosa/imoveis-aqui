"""Regras do contrato (W12) — o painel e a API só chamam daqui.

- contratos_visiveis_para(): quem enxerga qual contrato.
- salvar_contrato(): grava o contrato e gera (ou gera de novo) as parcelas.
- assinar_contrato(): marca a assinatura e tira o imóvel da vitrine.
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from contas.models import Usuario
from imoveis.models import Imovel

from .calculos import valores_das_parcelas, vencimentos
from .models import Contrato, Parcela


def contratos_visiveis_para(usuario):
    """Sempre filtra pela empresa do usuário e depois pelo perfil.

    - gestor: todos os contratos da empresa
    - corretor: só os contratos em que ele é o corretor (só leitura)
    - administrador (sem empresa): nenhum
    """
    if not usuario.is_authenticated or usuario.empresa_id is None:
        return Contrato.objects.none()
    contratos = Contrato.objects.filter(empresa_id=usuario.empresa_id)
    if usuario.perfil == Usuario.Perfil.GESTOR:
        return contratos
    if usuario.perfil == Usuario.Perfil.CORRETOR:
        return contratos.filter(corretor=usuario)
    return contratos.none()


def calcular_parcelas(contrato):
    """Lista de (número, vencimento, valor) que o contrato deve ter.

    Venda: (valor − entrada) dividido em N, centavos que sobram na última.
    Locação: N aluguéis iguais, N = meses da vigência (calculado no clean()).
    """
    quantidade = contrato.numero_parcelas
    if contrato.tipo == Contrato.Tipo.LOCACAO:
        valores = [contrato.valor] * quantidade
    else:
        valores = valores_das_parcelas(contrato.valor - contrato.entrada, quantidade)
    datas = vencimentos(contrato.data_inicio, quantidade, contrato.dia_vencimento)
    return [(numero, data, valor) for numero, (data, valor) in enumerate(zip(datas, valores), start=1)]


def _gerar_parcelas(contrato):
    contrato.parcelas.all().delete()
    Parcela.objects.bulk_create([
        Parcela(empresa_id=contrato.empresa_id, contrato=contrato, numero=numero, vencimento=data, valor=valor)
        for numero, data, valor in calcular_parcelas(contrato)
    ])


def _campos_alterados(contrato, gravado):
    """Campos do negócio que estão diferentes do que está gravado no banco."""
    nomes = [Contrato._meta.get_field(campo).attname for campo in Contrato.CAMPOS_DO_NEGOCIO]
    return [
        campo for campo, nome in zip(Contrato.CAMPOS_DO_NEGOCIO, nomes)
        if getattr(gravado, nome) != getattr(contrato, nome)
    ]


@transaction.atomic
def salvar_contrato(contrato, usuario):
    """Grava o contrato. Ninguém digita parcela: elas saem daqui.

    - contrato novo: grava e gera todas as parcelas;
    - rascunho editado: se mudou valor, entrada, nº de parcelas, dia ou
      datas, apaga as parcelas e gera de novo; se mudou só observação
      (ou imóvel/cliente/corretor), as parcelas ficam como estão;
    - assinado: só observações podem mudar — o resto é recusado.

    `contrato.empresa` precisa estar definida (sempre a do usuário logado).
    Tudo dentro de uma transação: se algo falhar, nada fica pela metade.
    """
    novo = contrato.pk is None
    gravado = None
    if novo:
        contrato.criado_por = usuario
    else:
        # select_for_update: trava a linha até o fim da transação (duas edições ao mesmo tempo
        # não geram parcelas misturadas)
        gravado = Contrato.objects.select_for_update().get(pk=contrato.pk)
        if not gravado.pode_alterar_negocio():
            # na locação o nº de parcelas é calculado, não digitado — não conta como mudança
            calculado = {"numero_parcelas"} if gravado.tipo == Contrato.Tipo.LOCACAO else set()
            if set(_campos_alterados(contrato, gravado)) - calculado:
                raise ValidationError(
                    "Contrato assinado: valores, datas, imóvel, cliente e corretor não podem mais "
                    "ser alterados. Só as observações."
                )

    # o clean() também calcula o nº de parcelas da locação, então vem antes da comparação abaixo
    contrato.full_clean()
    alterados = _campos_alterados(contrato, gravado) if gravado else []

    contrato.save()
    if novo or any(campo in Contrato.CAMPOS_DAS_PARCELAS for campo in alterados):
        _gerar_parcelas(contrato)
    return contrato


SITUACAO_DO_IMOVEL_APOS_ASSINAR = {
    Contrato.Tipo.VENDA: Imovel.Situacao.VENDIDO,
    Contrato.Tipo.LOCACAO: Imovel.Situacao.ALUGADO,
}
# só imóvel ainda livre pode ser vendido/alugado (reservado é o caso comum: sinal pago)
SITUACOES_QUE_ACEITAM_ASSINATURA = (Imovel.Situacao.DISPONIVEL, Imovel.Situacao.RESERVADO)


@transaction.atomic
def assinar_contrato(contrato, usuario, data_assinatura):
    """Marca o contrato como assinado e tira o imóvel da vitrine (critério 1).

    Tudo numa transação só — ou acontece tudo, ou nada:
    1. trava a linha do imóvel (select_for_update): se dois gestores
       assinarem dois contratos do mesmo imóvel ao mesmo tempo, o segundo
       espera o primeiro terminar e aí vê que já existe um assinado;
    2. confere que o contrato é rascunho, a data não está no futuro, não há
       outro contrato assinado para o imóvel e o imóvel ainda está livre;
    3. grava situação, data e quem assinou;
    4. muda a situação do imóvel para vendido/alugado pelo mudar_situacao()
       da W08 — que grava o histórico. Como a vitrine só mostra imóvel
       disponível (Imovel.objects.na_vitrine()), ele sai dela na hora.
    """
    imovel = Imovel.objects.select_for_update().get(pk=contrato.imovel_id)
    contrato = Contrato.objects.select_for_update().get(pk=contrato.pk)

    if contrato.assinado:
        raise ValidationError("Este contrato já está assinado.")
    if data_assinatura is None:
        raise ValidationError({"data_assinatura": "Informe a data da assinatura."})
    if data_assinatura > timezone.localdate():
        raise ValidationError({"data_assinatura": "A data da assinatura não pode estar no futuro."})

    outro = Contrato.objects.filter(imovel=imovel, situacao=Contrato.Situacao.ASSINADO).first()
    if outro:
        raise ValidationError(
            f"O imóvel {imovel.codigo} já tem um contrato assinado (#{outro.pk}). "
            "Um imóvel não pode ter dois contratos assinados ao mesmo tempo."
        )
    if imovel.situacao not in SITUACOES_QUE_ACEITAM_ASSINATURA:
        raise ValidationError(
            f"O imóvel {imovel.codigo} está {imovel.get_situacao_display().lower()} — "
            "só imóvel disponível ou reservado pode ter contrato assinado."
        )

    # as regras do cadastro valem de novo (ex.: a finalidade do imóvel mudou depois do rascunho)
    contrato.full_clean()

    contrato.situacao = Contrato.Situacao.ASSINADO
    contrato.data_assinatura = data_assinatura
    contrato.assinado_por = usuario
    try:
        # savepoint próprio: se o banco recusar (a regra "um assinado por imóvel"),
        # a mensagem sai em português em vez de um erro 500
        with transaction.atomic():
            contrato.save(update_fields=["situacao", "data_assinatura", "assinado_por", "atualizado_em"])
    except IntegrityError:
        raise ValidationError("Este imóvel já tem um contrato assinado.")

    imovel.mudar_situacao(
        SITUACAO_DO_IMOVEL_APOS_ASSINAR[contrato.tipo],
        usuario,
        f"Contrato #{contrato.pk} de {contrato.get_tipo_display().lower()} assinado em "
        f"{data_assinatura:%d/%m/%Y}.",
    )
    return contrato
