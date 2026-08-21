from django import forms
from .models import Produto, Cliente


class ProdutoForm(forms.ModelForm):
    """
    Formulário para cadastro e edição de produtos no painel administrativo.
    """
    class Meta:
        model = Produto
        fields = ['nome', 'categoria', 'preco', 'descricao', 'imagem', 'disponivel_hoje', 'limite_diario']
        widgets = {
            'nome': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ex: Bolo de Chocolate'}),
            'categoria': forms.Select(attrs={'class': 'form-select'}),
            'preco': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}),
            'descricao': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Descrição do produto...'}),
            'imagem': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'disponivel_hoje': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'limite_diario': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': '0'}),
        }


class ClienteForm(forms.ModelForm):
    """
    Formulário para cadastro e edição de clientes no painel administrativo.
    """
    senha = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Senha de acesso (deixe em branco para manter a atual se for edição)'
        }),
        required=False,
        label="Senha de Acesso"
    )

    class Meta:
        model = Cliente
        fields = ['nome', 'whatsapp', 'endereco', 'observacao', 'ativo']
        widgets = {
            'nome': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ex: Carlos Souza'}),
            'whatsapp': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ex: +5511999999999'}),
            'endereco': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Endereço (Opcional)...'}),
            'observacao': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Observações internas...'}),
            'ativo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        nova_senha = self.cleaned_data.get('senha')
        
        if nova_senha:
            instance.senha = nova_senha
        elif not instance.pk:
            # Para novos registros, se não foi fornecido, adicionamos erro de validação
            self.add_error('senha', 'A senha é obrigatória para novos clientes.')
            
        if commit:
            instance.save()
        return instance
