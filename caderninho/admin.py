from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html
from django.core.exceptions import ValidationError

from .models import (
    Cliente,
    Produto,
    Pedido,
    ItemPedido,
    FechamentoMensal,
    ConfiguracaoGeral
)
from .services import (
    fechar_mes_cliente,
    marcar_fechamento_como_pago,
    gerar_mensagem_whatsapp
)


class ItemPedidoInline(admin.TabularInline):
    model = ItemPedido
    extra = 1
    raw_id_fields = ('produto',)
    fields = ('produto', 'quantidade', 'preco_unitario')
    readonly_fields = ('preco_unitario',)  # Popula no save automaticamente


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('nome', 'whatsapp', 'ativo')
    search_fields = ('nome', 'whatsapp')
    list_filter = ('ativo',)
    actions = ['fechar_caderninho_mes_atual']

    @admin.action(description="Fechar caderninho do mês atual dos clientes selecionados")
    def fechar_caderninho_mes_atual(self, request, queryset):
        hoje = timezone.localtime(timezone.now())
        mes = hoje.month
        ano = hoje.year

        sucesso = 0
        erros = 0

        for cliente in queryset:
            try:
                fechar_mes_cliente(cliente, ano, mes)
                sucesso += 1
            except ValidationError as e:
                # Caso não existam pedidos em aberto no mês
                self.message_user(
                    request,
                    f"Aviso para {cliente.nome}: {e.message}",
                    level=messages.WARNING
                )
                erros += 1

        if sucesso > 0:
            self.message_user(
                request,
                f"Sucesso: {sucesso} fechamento(s) mensal(is) realizado(s) com sucesso para {mes:02d}/{ano}.",
                level=messages.SUCCESS
            )


@admin.register(Produto)
class ProdutoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'categoria', 'preco', 'disponivel_hoje', 'limite_diario')
    list_filter = ('categoria', 'disponivel_hoje')
    search_fields = ('nome',)
    list_editable = ('disponivel_hoje', 'limite_diario')


@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'cliente', 'data_criacao', 'status', 'status_financeiro', 'total_pedido_display'
    )
    list_filter = ('status', 'status_financeiro', 'data_criacao')
    search_fields = ('cliente__nome', 'id')
    inlines = [ItemPedidoInline]
    raw_id_fields = ('cliente', 'fechamento_mensal')

    actions = [
        'marcar_como_preparando',
        'marcar_como_entregue',
        'marcar_como_pago',
        'cancelar_pedidos'
    ]

    def total_pedido_display(self, obj):
        return f"R$ {obj.total_pedido:.2f}"
    total_pedido_display.short_description = "Valor Total"

    @admin.action(description="Alterar status para: Preparando")
    def marcar_como_preparando(self, request, queryset):
        rows = queryset.update(status='PREPARANDO')
        self.message_user(request, f"{rows} pedido(s) marcado(s) como Preparando.", level=messages.SUCCESS)

    @admin.action(description="Alterar status para: Entregue/Registrado")
    def marcar_como_entregue(self, request, queryset):
        rows = queryset.update(status='ENTREGUE')
        self.message_user(request, f"{rows} pedido(s) marcado(s) como Entregue.", level=messages.SUCCESS)

    @admin.action(description="Alterar status financeiro para: Pago")
    def marcar_como_pago(self, request, queryset):
        rows = queryset.update(status_financeiro='PAGO')
        self.message_user(request, f"{rows} pedido(s) marcado(s) como Pago.", level=messages.SUCCESS)

    @admin.action(description="Cancelar pedidos selecionados")
    def cancelar_pedidos(self, request, queryset):
        rows = queryset.update(status='CANCELADO')
        self.message_user(request, f"{rows} pedido(s) cancelado(s).", level=messages.SUCCESS)


@admin.register(FechamentoMensal)
class FechamentoMensalAdmin(admin.ModelAdmin):
    list_display = (
        'cliente', 'periodo_display', 'total_devedor', 'pago', 'data_fechamento', 'cobrar_whatsapp_btn'
    )
    list_filter = ('pago', 'ano', 'mes')
    search_fields = ('cliente__nome',)
    readonly_fields = ('total_devedor', 'data_fechamento', 'data_pagamento')
    actions = ['marcar_fechamentos_como_pago']

    def periodo_display(self, obj):
        return f"{obj.mes:02d}/{obj.ano}"
    periodo_display.short_description = "Mês de Referência"

    def cobrar_whatsapp_btn(self, obj):
        if obj.pago:
            return format_html(
                '<span style="color: green; font-weight: bold;">Pago</span>'
            )

        try:
            url = gerar_mensagem_whatsapp(obj)
            return format_html(
                '<a class="button" href="{}" target="_blank" '
                'style="background-color: #25D366; color: white; font-weight: bold; '
                'padding: 5px 10px; border-radius: 4px; text-decoration: none; display: inline-block;">'
                '💬 Cobrar via WhatsApp</a>',
                url
            )
        except Exception as e:
            return format_html(
                f'<span style="color: red; font-size: 11px;">Erro: {str(e)}</span>'
            )
    cobrar_whatsapp_btn.short_description = "Link de Cobrança"

    @admin.action(description="Marcar fechamentos selecionados como PAGO (Liquidar)")
    def marcar_fechamentos_como_pago(self, request, queryset):
        sucesso = 0
        for fechamento in queryset.filter(pago=False):
            marcar_fechamento_como_pago(fechamento)
            sucesso += 1

        if sucesso > 0:
            self.message_user(
                request,
                f"Sucesso: {sucesso} fechamento(s) mensal(is) liquidado(s) e atualizado(s) no sistema.",
                level=messages.SUCCESS
            )


@admin.register(ConfiguracaoGeral)
class ConfiguracaoGeralAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'chave_pix', 'tipo_chave_pix', 'nome_titular')

    def has_add_permission(self, request):
        # Evita a criação de mais de uma instância
        return not ConfiguracaoGeral.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Desativa a exclusão das configurações
        return False
