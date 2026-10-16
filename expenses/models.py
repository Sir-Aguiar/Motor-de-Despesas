from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction


class Company(models.Model):
    name = models.CharField('name', max_length=255)
    cnpj = models.CharField('CNPJ', max_length=14, unique=True)
    created_at = models.DateTimeField('created at', auto_now_add=True)

    class Meta:
        verbose_name = 'company'
        verbose_name_plural = 'companies'
        ordering = ['name']

    def __str__(self):
        return self.name


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Admin'
        APPROVER = 'APPROVER', 'Approver'
        EMPLOYEE = 'EMPLOYEE', 'Employee'

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name='users',
        null=True,
        blank=True,
        verbose_name='company',
    )
    role = models.CharField(
        'role',
        max_length=20,
        choices=Role.choices,
        default=Role.EMPLOYEE,
    )

    class Meta:
        verbose_name = 'user'
        verbose_name_plural = 'users'

    def __str__(self):
        return self.get_username()


class ExpenseStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Draft'
    SUBMITTED = 'SUBMITTED', 'Submitted'
    APPROVED = 'APPROVED', 'Approved'
    REJECTED = 'REJECTED', 'Rejected'
    PAID = 'PAID', 'Paid'


ALLOWED_STATUS_TRANSITIONS = {
    ExpenseStatus.DRAFT: {ExpenseStatus.SUBMITTED},
    ExpenseStatus.SUBMITTED: {ExpenseStatus.APPROVED, ExpenseStatus.REJECTED},
    ExpenseStatus.APPROVED: {ExpenseStatus.PAID},
    ExpenseStatus.REJECTED: set(),
    ExpenseStatus.PAID: set(),
}


class Expense(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name='expenses',
        verbose_name='company',
    )
    requester = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='expenses',
        verbose_name='requester',
    )
    amount = models.DecimalField(
        'amount',
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    description = models.TextField('description')
    status = models.CharField(
        'status',
        max_length=20,
        choices=ExpenseStatus.choices,
        default=ExpenseStatus.DRAFT,
    )
    created_at = models.DateTimeField('created at', auto_now_add=True)
    updated_at = models.DateTimeField('updated at', auto_now=True)

    class Meta:
        verbose_name = 'expense'
        verbose_name_plural = 'expenses'
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name='expense_amount_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=ExpenseStatus.values),
                name='expense_status_valid',
            ),
        ]
        indexes = [
            models.Index(fields=['company', 'status']),
            models.Index(fields=['company', 'created_at']),
        ]

    def __str__(self):
        return f'{self.description[:40]} ({self.get_status_display()})'

    def clean(self):
        super().clean()
        self._validate_amount()
        self._validate_company_scope()
        previous_status = self._previous_status()
        if previous_status is None:
            if self.status != ExpenseStatus.DRAFT:
                raise ValidationError(
                    {'status': 'A new expense must start as DRAFT.'}
                )
            return
        if previous_status != self.status:
            self.validate_transition(previous_status, self.status)

    def save(self, *args, actor=None, **kwargs):
        update_fields = kwargs.get('update_fields')
        with transaction.atomic():
            previous_status = self._locked_previous_status()
            creating = previous_status is None
            status_will_persist = update_fields is None or 'status' in update_fields

            if creating:
                if not self.status:
                    self.status = ExpenseStatus.DRAFT
                if self.status != ExpenseStatus.DRAFT:
                    raise ValidationError(
                        {'status': 'A new expense must start as DRAFT.'}
                    )
            elif status_will_persist and previous_status != self.status:
                self.validate_transition(previous_status, self.status)
                if actor is None:
                    raise ValidationError(
                        {
                            'status': (
                                'Provide the user who changed the expense status.'
                            )
                        }
                    )

            self._validate_amount()
            self._validate_company_scope()

            status_changed = (
                not creating
                and status_will_persist
                and previous_status != self.status
            )
            super().save(*args, **kwargs)
            if status_changed:
                AuditLog.objects.create(
                    expense=self,
                    changed_by=actor,
                    previous_status=previous_status,
                    new_status=self.status,
                )

    def change_status(self, new_status, changed_by):
        self.status = new_status
        self.save(actor=changed_by)

    @staticmethod
    def validate_transition(previous_status, new_status):
        allowed = ALLOWED_STATUS_TRANSITIONS.get(previous_status, set())
        if new_status not in allowed:
            raise ValidationError(
                {
                    'status': (
                        f'Transition from {previous_status} to {new_status} '
                        'is not allowed.'
                    )
                }
            )

    def _validate_amount(self):
        if self.amount is None or self.amount <= 0:
            raise ValidationError(
                {'amount': 'The expense amount must be greater than zero.'}
            )

    def _validate_company_scope(self):
        if not self.requester_id or not self.company_id:
            return
        requester_company_id = (
            User.objects.filter(pk=self.requester_id)
            .values_list('company_id', flat=True)
            .first()
        )
        if requester_company_id != self.company_id:
            raise ValidationError(
                {
                    'requester': (
                        'The requester must belong to the expense company.'
                    )
                }
            )

    def _previous_status(self):
        if self._state.adding or not self.pk:
            return None
        return (
            type(self).objects.filter(pk=self.pk)
            .values_list('status', flat=True)
            .first()
        )

    def _locked_previous_status(self):
        if self._state.adding or not self.pk:
            return None
        return (
            type(self).objects.select_for_update()
            .filter(pk=self.pk)
            .values_list('status', flat=True)
            .first()
        )


class AuditLog(models.Model):
    expense = models.ForeignKey(
        Expense,
        on_delete=models.PROTECT,
        related_name='audit_logs',
        verbose_name='expense',
    )
    changed_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='audit_logs',
        verbose_name='changed by',
    )
    created_at = models.DateTimeField('changed at', auto_now_add=True)
    previous_status = models.CharField(
        'previous status',
        max_length=20,
        choices=ExpenseStatus.choices,
    )
    new_status = models.CharField(
        'new status',
        max_length=20,
        choices=ExpenseStatus.choices,
    )

    class Meta:
        verbose_name = 'audit log'
        verbose_name_plural = 'audit logs'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['expense', 'created_at']),
        ]

    def __str__(self):
        return (
            f'{self.expense_id}: {self.previous_status} → {self.new_status}'
        )
