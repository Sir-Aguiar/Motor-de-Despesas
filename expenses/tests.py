from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from expenses.models import (
    ALLOWED_STATUS_TRANSITIONS,
    AuditLog,
    Company,
    Expense,
    ExpenseStatus,
    User,
)


class ExpenseWorkflowTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name='Acme Inc',
            cnpj='12345678000199',
        )
        self.other_company = Company.objects.create(
            name='Other Inc',
            cnpj='99887766000155',
        )
        self.employee = User.objects.create_user(
            username='ana',
            password='secure-password',
            company=self.company,
            role=User.Role.EMPLOYEE,
        )
        self.approver = User.objects.create_user(
            username='bruno',
            password='secure-password',
            company=self.company,
            role=User.Role.APPROVER,
        )
        self.outsider = User.objects.create_user(
            username='carla',
            password='secure-password',
            company=self.other_company,
            role=User.Role.EMPLOYEE,
        )

    def _expense(self, **overrides):
        data = {
            'company': self.company,
            'requester': self.employee,
            'amount': Decimal('150.75'),
            'description': 'Client visit',
        }
        data.update(overrides)
        return Expense.objects.create(**data)

    def test_new_expense_starts_as_draft(self):
        expense = self._expense()

        self.assertEqual(expense.status, ExpenseStatus.DRAFT)
        self.assertEqual(expense.amount, Decimal('150.75'))
        self.assertFalse(expense.audit_logs.exists())

    def test_allowed_path_records_each_status_change(self):
        expense = self._expense()

        expense.change_status(ExpenseStatus.SUBMITTED, self.employee)
        expense.change_status(ExpenseStatus.APPROVED, self.approver)
        expense.change_status(ExpenseStatus.PAID, self.approver)

        expense.refresh_from_db()
        self.assertEqual(expense.status, ExpenseStatus.PAID)
        logs = list(expense.audit_logs.order_by('created_at'))
        self.assertEqual(
            [(log.previous_status, log.new_status, log.changed_by) for log in logs],
            [
                (ExpenseStatus.DRAFT, ExpenseStatus.SUBMITTED, self.employee),
                (ExpenseStatus.SUBMITTED, ExpenseStatus.APPROVED, self.approver),
                (ExpenseStatus.APPROVED, ExpenseStatus.PAID, self.approver),
            ],
        )

    def test_submitted_expense_can_be_rejected(self):
        expense = self._expense()
        expense.change_status(ExpenseStatus.SUBMITTED, self.employee)

        expense.change_status(ExpenseStatus.REJECTED, self.approver)

        expense.refresh_from_db()
        self.assertEqual(expense.status, ExpenseStatus.REJECTED)
        self.assertEqual(expense.audit_logs.count(), 2)

    def test_rejected_and_paid_are_terminal(self):
        self.assertEqual(ALLOWED_STATUS_TRANSITIONS[ExpenseStatus.REJECTED], set())
        self.assertEqual(ALLOWED_STATUS_TRANSITIONS[ExpenseStatus.PAID], set())

        expense = self._expense()
        expense.change_status(ExpenseStatus.SUBMITTED, self.employee)
        expense.change_status(ExpenseStatus.REJECTED, self.approver)

        with self.assertRaises(ValidationError):
            expense.change_status(ExpenseStatus.PAID, self.approver)

    def test_skips_and_backward_moves_are_rejected(self):
        expense = self._expense()

        with self.assertRaises(ValidationError):
            expense.change_status(ExpenseStatus.APPROVED, self.approver)

        expense.change_status(ExpenseStatus.SUBMITTED, self.employee)
        with self.assertRaises(ValidationError):
            expense.change_status(ExpenseStatus.DRAFT, self.employee)
        with self.assertRaises(ValidationError):
            expense.change_status(ExpenseStatus.PAID, self.approver)

        expense.refresh_from_db()
        self.assertEqual(expense.status, ExpenseStatus.SUBMITTED)
        self.assertEqual(expense.audit_logs.count(), 1)

    def test_status_change_requires_actor(self):
        expense = self._expense()
        expense.status = ExpenseStatus.SUBMITTED

        with self.assertRaises(ValidationError):
            expense.save()

        self.assertFalse(AuditLog.objects.exists())

    def test_new_expense_cannot_start_outside_draft(self):
        with self.assertRaises(ValidationError):
            self._expense(status=ExpenseStatus.SUBMITTED)

    def test_requester_must_belong_to_the_expense_company(self):
        with self.assertRaises(ValidationError):
            self._expense(requester=self.outsider)

    def test_amount_must_be_positive(self):
        with self.assertRaises(ValidationError):
            self._expense(amount=Decimal('0.00'))

    def test_cnpj_is_unique(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Company.objects.create(name='Duplicate', cnpj='12345678000199')
