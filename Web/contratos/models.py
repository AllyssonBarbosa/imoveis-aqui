from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from core.models import EmpresaOwnedModel

from .calculos import meses_de_vigencia

LIMITE_DE_PARCELAS = 600  # 50 anos — acima disso é quase certo erro de digitação


class Contrato(EmpresaOwnedModel):
    """O negócio fechado: venda ou locação de um imóvel para um cliente (W12).

    - Venda: `valor` é o preço total; `entrada` sai do total e o resto é
      dividido em `numero_parcelas`.
    - Locação: `valor` é o aluguel mensal; não há entrada, e o número de
      parcelas é calculado pela vigência (data_inicio → data_fim).

    As parcelas são geradas pelo servidor ao salvar (contratos/servicos.py),
    nunca digitadas. Depois de assinado, os campos do negócio ficam travados.
    """

    class Tipo(models.TextChoices):
        VENDA = "VENDA", "Venda"
        LOCACAO = "LOCACAO", "Locação"

    class Situacao(models.TextChoices):
        RASCUNHO = "RASCUNHO", "Rascunho"
        ASSINADO = "ASSINADO", "Assinado"

    # campos que definem as parcelas: mudou algum deles, as parcelas são geradas de novo
    CAMPOS_DAS_PARCELAS = [
        "tipo", "valor", "entrada", "numero_parcelas", "dia_vencimento", "data_inicio", "data_fim",
    ]
    # campos do negócio: travados depois da assinatura (só observações continuam editáveis)
    CAMPOS_DO_NEGOCIO = CAMPOS_DAS_PARCELAS + ["imovel", "cliente", "corretor"]

    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    imovel = models.ForeignKey(
        "imoveis.Imovel", on_delete=models.PROTECT, related_name="contratos", verbose_name="imóvel"
    )
    cliente = models.ForeignKey("clientes.Cliente", on_delete=models.PROTECT, related_name="contratos")
    corretor = models.ForeignKey(
        "contas.Usuario", on_delete=models.PROTECT, related_name="contratos_como_corretor"
    )
    valor = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"), "O valor precisa ser maior que zero.")],
        help_text="Venda: preço total. Locação: aluguel mensal.",
    )
    entrada = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        validators=[MinValueValidator(Decimal("0"), "A entrada não pode ser negativa.")],
        help_text="Só na venda. Sai do valor total antes de dividir as parcelas.",
    )
    numero_parcelas = models.PositiveSmallIntegerField(
        "número de parcelas", null=True, blank=True,
        validators=[
            MinValueValidator(1, "Informe pelo menos 1 parcela."),
            MaxValueValidator(LIMITE_DE_PARCELAS, f"O máximo é {LIMITE_DE_PARCELAS} parcelas."),
        ],
        help_text="Só na venda. Na locação é calculado pela vigência.",
    )
    dia_vencimento = models.PositiveSmallIntegerField(
        "dia de vencimento",
        validators=[
            MinValueValidator(1, "O dia de vencimento vai de 1 a 31."),
            MaxValueValidator(31, "O dia de vencimento vai de 1 a 31."),
        ],
        help_text="Em mês sem esse dia (ex.: 31 em fevereiro), vence no último dia do mês.",
    )
    data_inicio = models.DateField(
        "data de início",
        help_text="Venda: data do negócio. Locação: início da vigência. "
                  "A 1ª parcela vence no mês seguinte.",
    )
    data_fim = models.DateField("fim da vigência", null=True, blank=True, help_text="Só na locação.")
    observacoes = models.TextField("observações", blank=True)

    # editable=False: não entram em formulário — mudam só pelas regras do servidor
    situacao = models.CharField(
        "situação", max_length=10, choices=Situacao.choices, default=Situacao.RASCUNHO, editable=False
    )
    data_assinatura = models.DateField("data da assinatura", null=True, blank=True, editable=False)
    assinado_por = models.ForeignKey(
        "contas.Usuario", on_delete=models.PROTECT, null=True, blank=True, editable=False,
        related_name="contratos_assinados",
    )
    criado_por = models.ForeignKey(
        "contas.Usuario", on_delete=models.PROTECT, editable=False, related_name="contratos_criados"
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "contrato"
        verbose_name_plural = "contratos"
        ordering = ["-criado_em", "-id"]
        constraints = [
            # mesmo que alguém pule a checagem do servidor, o banco não aceita
            # dois contratos assinados para o mesmo imóvel
            models.UniqueConstraint(
                fields=["imovel"],
                condition=models.Q(situacao="ASSINADO"),
                name="um_contrato_assinado_por_imovel",
                violation_error_message="Este imóvel já tem um contrato assinado.",
            ),
            models.CheckConstraint(
                condition=models.Q(dia_vencimento__gte=1, dia_vencimento__lte=31),
                name="contrato_dia_vencimento_de_1_a_31",
            ),
            models.CheckConstraint(
                condition=models.Q(entrada__gte=0) & models.Q(entrada__lt=models.F("valor")),
                name="contrato_entrada_menor_que_valor",
            ),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()} #{self.pk} — {self.imovel}"

    @property
    def total_parcelado(self):
        """Venda: valor − entrada. Locação: aluguel × meses."""
        if self.tipo == self.Tipo.LOCACAO:
            return self.valor * (self.numero_parcelas or 0)
        return self.valor - self.entrada

    @property
    def assinado(self):
        return self.situacao == self.Situacao.ASSINADO

    def pode_alterar_negocio(self):
        """Valores, datas, imóvel, cliente e corretor só mudam enquanto é rascunho."""
        return self.situacao == self.Situacao.RASCUNHO

    def clean(self):
        super().clean()
        erros = {}
        self._conferir_mesma_empresa(erros)
        self._conferir_finalidade(erros)

        if self.tipo == self.Tipo.VENDA:
            if self.data_fim:
                erros["data_fim"] = "Vigência é só da locação — deixe em branco na venda."
            if not self.numero_parcelas:
                erros.setdefault("numero_parcelas", "Informe o número de parcelas.")
            if self.valor is not None and self.entrada is not None and self.entrada >= self.valor:
                erros["entrada"] = "A entrada precisa ser menor que o valor total."

        elif self.tipo == self.Tipo.LOCACAO:
            if self.entrada:
                erros["entrada"] = "Locação não tem entrada — deixe em branco ou zero."
            if not self.data_fim:
                erros["data_fim"] = "Informe o fim da vigência da locação."
            elif self.data_inicio:
                try:
                    # o número de aluguéis sai da vigência — o que veio digitado é ignorado
                    self.numero_parcelas = meses_de_vigencia(self.data_inicio, self.data_fim)
                except ValidationError as erro:
                    erros.update(erro.message_dict)
                else:
                    if self.numero_parcelas > LIMITE_DE_PARCELAS:
                        erros["data_fim"] = f"A vigência não pode passar de {LIMITE_DE_PARCELAS} meses."

        if erros:
            raise ValidationError(erros)

    def _conferir_mesma_empresa(self, erros):
        # sem empresa ainda (a view define antes do full_clean), não há o que comparar
        if not self.empresa_id:
            return
        if self.imovel_id and self.imovel.empresa_id != self.empresa_id:
            erros["imovel"] = "O imóvel precisa ser da mesma empresa."
        if self.cliente_id and self.cliente.empresa_id != self.empresa_id:
            erros["cliente"] = "O cliente precisa ser da mesma empresa."
        if self.corretor_id:
            from contas.models import Usuario

            if self.corretor.empresa_id != self.empresa_id:
                erros["corretor"] = "O corretor precisa ser da mesma empresa."
            elif self.corretor.perfil != Usuario.Perfil.CORRETOR:
                erros["corretor"] = "Escolha um usuário com perfil de corretor."

    def _conferir_finalidade(self, erros):
        """Contrato de venda só para imóvel à venda; de locação, só para imóvel para alugar."""
        if not self.imovel_id or not self.tipo or "imovel" in erros:
            return
        from imoveis.models import Imovel

        aceitas = {
            self.Tipo.VENDA: (Imovel.Finalidade.VENDA, Imovel.Finalidade.VENDA_E_ALUGUEL),
            self.Tipo.LOCACAO: (Imovel.Finalidade.ALUGUEL, Imovel.Finalidade.VENDA_E_ALUGUEL),
        }[self.tipo]
        if self.imovel.finalidade not in aceitas:
            erros["imovel"] = (
                f"Este imóvel está anunciado para {self.imovel.get_finalidade_display().lower()} — "
                f"não dá para fazer um contrato de {self.get_tipo_display().lower()}."
            )


class Parcela(EmpresaOwnedModel):
    """Uma parcela do contrato, gerada pelo servidor: número, vencimento e valor.

    Não tem campo de situação: "paga", "a vencer" ou "atrasada" é calculado
    na hora da consulta, pela data de hoje e pelas baixas (etapas 5 e 6).
    A empresa é sempre a do contrato (copiada no save) — assim a API e os
    relatórios filtram parcela pela empresa direto, como todo dado do acervo.
    """

    contrato = models.ForeignKey(Contrato, on_delete=models.CASCADE, related_name="parcelas")
    numero = models.PositiveSmallIntegerField("número")
    vencimento = models.DateField()
    valor = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        verbose_name = "parcela"
        verbose_name_plural = "parcelas"
        ordering = ["contrato", "numero"]
        constraints = [
            models.UniqueConstraint(
                fields=["contrato", "numero"],
                name="parcela_numero_unico_por_contrato",
                violation_error_message="Este contrato já tem uma parcela com esse número.",
            ),
            models.CheckConstraint(condition=models.Q(valor__gt=0), name="parcela_valor_positivo"),
        ]

    def __str__(self):
        return f"Parcela {self.numero} do contrato #{self.contrato_id}"

    def save(self, *args, **kwargs):
        self.empresa_id = self.contrato.empresa_id
        super().save(*args, **kwargs)
