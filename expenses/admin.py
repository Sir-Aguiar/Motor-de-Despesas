from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from expenses.models import AuditLog, Company, Expense, User


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ('name', 'cnpj', 'created_at')
    search_fields = ('name', 'cnpj')
    readonly_fields = ('created_at',)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (
        ('Company', {'fields': ('company', 'role')}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ('Company', {'fields': ('company', 'role')}),
    )
    list_display = ('username', 'email', 'company', 'role', 'is_staff')
    list_filter = ('role', 'is_staff', 'is_superuser', 'company')
    search_fields = ('username', 'email', 'first_name', 'last_name')


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'company',
        'requester',
        'amount',
        'status',
        'created_at',
        'updated_at',
    )
    list_filter = ('status', 'company')
    search_fields = ('description', 'requester__username', 'company__name')
    readonly_fields = ('created_at', 'updated_at')

    def save_model(self, request, obj, form, change):
        if change and 'status' in form.changed_data:
            obj.save(actor=request.user)
            return
        obj.save()


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        'expense',
        'changed_by',
        'previous_status',
        'new_status',
        'created_at',
    )
    list_filter = ('previous_status', 'new_status')
    search_fields = ('expense__description', 'changed_by__username')
    readonly_fields = (
        'expense',
        'changed_by',
        'previous_status',
        'new_status',
        'created_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
