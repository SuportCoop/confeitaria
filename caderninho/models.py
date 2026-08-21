from django.core.validators import RegexValidator
from django.db import models
from django.contrib.auth.hashers import make_password


class Cliente(models.Model):
    """
    Representa um cliente do caderninho digital.
    """
    nome = models.CharField(
        max_length=150,
        verbose_name="Nome Completo"
    )
    whatsapp = models.CharField(
        max_length=20,
        validators=[
            RegexValidator(
                regex=r'^\+?1?\d{9,15}$',
                message="O número de WhatsApp deve ser inserido no formato: '+5511999999999'. De 9 a 15 dígitos."
            )
        ],
        verbose_name="WhatsApp"
    )
    endereco = models.TextField(
        blank=True,
        null=True,
        verbose_name="Endereço (Opcional)"
    )
    observacao = models.TextField(
        blank=True,
        null=True,
        verbose_name="Observações Internas"
    )
    ativo = models.BooleanField(
        default=True,
        verbose_name="Cliente Ativo"
    )
    senha = models.CharField(
        max_length=128,
        default="",
        verbose_name="Senha de Acesso",
        help_text="A senha será criptografada automaticamente ao salvar."
    )

    class Meta:
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"
        ordering = ['nome']

    def save(self, *args, **kwargs):
        # Criptografa a senha se for nova ou alterada (se não for um hash já gerado)
        if self.senha and not self.senha.startswith(('pbkdf2_sha256$', 'bcrypt$', 'argon2$')):
            self.senha = make_password(self.senha)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.nome


class Produto(models.Model):
    """
    Representa um produto oferecido pela loja.
    """
    CATEGORIA_CHOICES = [
        ('DOCE', 'Doce'),
        ('SALGADO', 'Salgado'),
        ('BOLO', 'Bolo'),
    ]

    nome = models.CharField(
        max_length=150,
        verbose_name="Nome do Produto"
    )
    categoria = models.CharField(
        max_length=10,
        choices=CATEGORIA_CHOICES,
        verbose_name="Categoria"
    )
    preco = models.DecimalField(
        max_length=10,
        max_digits=10,
        decimal_places=2,
        verbose_name="Preço Unitário"
    )
    descricao = models.TextField(
        blank=True,
        null=True,
        verbose_name="Descrição"
    )
    imagem = models.ImageField(
        upload_to='produtos/',
        blank=True,
        null=True,
        verbose_name="Imagem do Produto"
    )
    disponivel_hoje = models.BooleanField(
        default=True,
        verbose_name="Disponível Hoje"
    )
    limite_diario = models.PositiveIntegerField(
        default=0,
        help_text="Defina como 0 para produção ilimitada.",
        verbose_name="Limite de Produção Diário"
    )

    class Meta:
        verbose_name = "Produto"
        verbose_name_plural = "Produtos"
        ordering = ['nome']

    def __str__(self):
        return f"{self.nome} ({self.get_categoria_display()}) - R$ {self.preco}"


class FechamentoMensal(models.Model):
    """
    Consolida o consumo do cliente em um determinado mês e ano.
    """
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.CASCADE,
        related_name="fechamentos",
        verbose_name="Cliente"
    )
    ano = models.PositiveIntegerField(verbose_name="Ano")
    mes = models.PositiveIntegerField(verbose_name="Mês")
    total_devedor = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="Total Devedor"
    )
    pago = models.BooleanField(
        default=False,
        verbose_name="Pago"
    )
    data_fechamento = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Data do Fechamento"
    )
    data_pagamento = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name="Data do Pagamento"
    )

    class Meta:
        verbose_name = "Fechamento Mensal"
        verbose_name_plural = "Fechamentos Mensais"
        ordering = ['-ano', '-mes', 'cliente__nome']
        unique_together = ('cliente', 'ano', 'mes')

    def __str__(self):
        mes_formatado = f"{self.mes:02d}/{self.ano}"
        status = "Pago" if self.pago else "Em Aberto"
        return f"Fechamento {self.cliente.nome} ({mes_formatado}) - R$ {self.total_devedor} [{status}]"


class Pedido(models.Model):
    """
    Representa um pedido de compra realizado.
    """
    STATUS_CHOICES = [
        ('PENDENTE', 'Pendente'),
        ('PREPARANDO', 'Preparando'),
        ('ENTREGUE', 'Entregue/Registrado'),
        ('CANCELADO', 'Cancelado'),
    ]

    STATUS_FINANCEIRO_CHOICES = [
        ('ABERTO', 'Aberto (A Pagar)'),
        ('PAGO', 'Pago'),
    ]

    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.CASCADE,
        related_name="pedidos",
        verbose_name="Cliente"
    )
    data_criacao = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Data de Criação"
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='PENDENTE',
        verbose_name="Status do Pedido"
    )
    status_financeiro = models.CharField(
        max_length=20,
        choices=STATUS_FINANCEIRO_CHOICES,
        default='ABERTO',
        verbose_name="Status Financeiro"
    )
    taxa_entrega = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0.00,
        verbose_name="Taxa de Entrega"
    )
    observacoes = models.TextField(
        blank=True,
        null=True,
        verbose_name="Observações do Pedido"
    )
    fechamento_mensal = models.ForeignKey(
        FechamentoMensal,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="pedidos",
        verbose_name="Fechamento Mensal Associado"
    )

    class Meta:
        verbose_name = "Pedido"
        verbose_name_plural = "Pedidos"
        ordering = ['-data_criacao']

    @property
    def total_pedido(self):
        """
        Calcula o valor total do pedido somando itens e taxa de entrega.
        """
        total_itens = sum(item.total_item for item in self.itens.all())
        return total_itens + self.taxa_entrega

    @property
    def whatsapp_link(self):
        """
        Gera a URL de cobrança individual deste pedido específico.
        """
        from .services import gerar_mensagem_whatsapp_pedido
        try:
            return gerar_mensagem_whatsapp_pedido(self)
        except Exception:
            return "#"

    def __str__(self):
        return f"Pedido #{self.id} - {self.cliente.nome} ({self.data_criacao.strftime('%d/%m/%Y')})"


class ItemPedido(models.Model):
    """
    Representa um item específico associado a um pedido.
    """
    pedido = models.ForeignKey(
        Pedido,
        on_delete=models.CASCADE,
        related_name="itens",
        verbose_name="Pedido"
    )
    produto = models.ForeignKey(
        Produto,
        on_delete=models.PROTECT,
        verbose_name="Produto"
    )
    quantidade = models.PositiveIntegerField(
        verbose_name="Quantidade"
    )
    preco_unitario = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        blank=True,
        verbose_name="Preço Unitário (Histórico)"
    )

    class Meta:
        verbose_name = "Item do Pedido"
        verbose_name_plural = "Itens do Pedido"

    @property
    def total_item(self):
        """
        Calcula o subtotal do item.
        """
        return self.quantidade * self.preco_unitario

    def save(self, *args, **kwargs):
        # Congela o preço unitário do produto na criação do item
        if not self.preco_unitario and self.produto:
            self.preco_unitario = self.produto.preco
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.quantidade}x {self.produto.nome} no Pedido #{self.pedido.id}"


class ConfiguracaoGeral(models.Model):
    """
    Configurações globais do sistema de vendas e cobrança. Model Singleton.
    """
    CHAVE_PIX_CHOICES = [
        ('CPF', 'CPF'),
        ('CNPJ', 'CNPJ'),
        ('EMAIL', 'E-mail'),
        ('TELEFONE', 'Telefone'),
        ('ALEATORIA', 'Chave Aleatória'),
    ]

    chave_pix = models.CharField(
        max_length=100,
        verbose_name="Chave PIX para Recebimento"
    )
    tipo_chave_pix = models.CharField(
        max_length=20,
        choices=CHAVE_PIX_CHOICES,
        default='EMAIL',
        verbose_name="Tipo da Chave PIX"
    )
    nome_titular = models.CharField(
        max_length=100,
        verbose_name="Nome do Titular do PIX"
    )
    cidade_titular = models.CharField(
        max_length=100,
        default="São Paulo",
        verbose_name="Cidade do Titular"
    )
    mensagem_padrao_whatsapp = models.TextField(
        verbose_name="Mensagem Padrão de Cobrança",
        help_text=(
            "Use as chaves: {nome}, {periodo}, {detalhes}, {total}, {chave_pix}, {copia_cola} "
            "para preencher dinamicamente na cobrança do WhatsApp."
        )
    )
    whatsapp_loja = models.CharField(
        max_length=20,
        default="+5511999999999",
        verbose_name="WhatsApp de Contato da Loja"
    )

    class Meta:
        verbose_name = "Configuração Geral"
        verbose_name_plural = "Configuração Geral"

    def save(self, *args, **kwargs):
        # Garante o padrão Singleton
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, created = cls.objects.get_or_create(
            pk=1,
            defaults={
                'chave_pix': 'financeiro@lojinha.com',
                'tipo_chave_pix': 'EMAIL',
                'nome_titular': 'Confeitaria da Lojinha LTDA',
                'cidade_titular': 'Sao Paulo',
                'whatsapp_loja': '+5511999999999',
                'mensagem_padrao_whatsapp': (
                    "Olá, *{nome}*! 🌸\n\n"
                    "Aqui está o fechamento do seu caderninho de *{periodo}*:\n\n"
                    "{detalhes}\n"
                    "*Taxas de Entrega:* R$ {taxa_entrega}\n"
                    "👉 *Valor Total Acumulado:* R$ {total}\n\n"
                    "Para facilitar, você pode realizar o PIX:\n"
                    "🔑 *Chave PIX ({tipo_chave}):* `{chave_pix}`\n"
                    "Titular: *{titular}*\n\n"
                    "Ou use o PIX Copia e Cola abaixo:\n\n"
                    "`{copia_cola}`\n\n"
                    "Ficamos no aguardo do comprovante. Muito obrigada pela preferência! 😊"
                )
            }
        )
        return obj

    def __str__(self):
        return "Configurações Gerais do Sistema"
