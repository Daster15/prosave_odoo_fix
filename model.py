# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
from odoo import fields, models, api, _
from markupsafe import Markup
import logging

_logger = logging.getLogger(__name__)

class ProsaveOrderData(models.Model):
    _name = 'prosave.order.data'
    _inherit = 'mail.thread'
    _description = 'Prosave Order Data'

    reference_id = fields.Char(string='Reference ID', readonly=True, copy=False, default='New')
    data = fields.Json(string='Prosave Data')
    status = fields.Selection([
        ('to_process', 'To Process'),
        ('processed', 'Processed'),
        ('error', 'Error')
    ], string='Status', default='to_process', required=True)
    purchase_order = fields.Many2one('purchase.order', string='Purchase Order', readonly=True)

    def action_set_in_process(self):
        """Reset records with status 'error' to 'to_process'."""
        self.filtered(lambda record: record.status == 'error').write({'status': 'to_process'})

    def cron_process_prosave_order(self):
        """Cron job to process 'to_process' records."""
        records = self.search([('status', '=', 'to_process')], limit=100)
        if not records:
            _logger.info(_("No records to process."))
            return

        for record in records:
            try:
                with self.env.cr.savepoint():
                    _logger.info("processing record: %s", record.reference_id)
                    record.process_prosave_order()
            except Exception as e:
                record._log_error([_("Error processing record: %s") % str(e)])

        if len(records) == 100:
            self.env.ref('flyt_prosave_webhook.ir_cron_process_prosave_po')._trigger()

    def process_prosave_order(self):
        """Process and validate Prosave data."""
        data = self.data
        if not isinstance(data, dict):
            self._log_error([_("Invalid data format.")])
            return

        errors, validated = self._validate_data(data)
        if errors:
            self._log_error(errors)
            return

        self._create_purchase_order(data, validated)

    def _validate_data(self, data):
        """Validate Prosave data and return errors if any."""
        errors = []

        # Validate company settings
        default_product_id = self.env.user.company_id.pro_save_po_default_product_id.id
        if not default_product_id:
            errors.append(_("Default product not configured."))

        # Validate order fields
        missing_fields = self._validate_order_data(data)
        if missing_fields:
            errors.append(_("Missing fields: %s") % ", ".join(missing_fields))

        # Format order number
        order_number, order_number_error = self._format_order_number(data.get('OrderNumber', ''))
        if order_number_error:
            errors.append(order_number_error)
        data['OrderNumber'] = order_number

        # Validate individual fields and cache results
        validated = {}
        for field, key, method in [
            ('UserEmail', 'user_id', self._get_user_id),
            ('Supplier', 'partner_id', self._get_supplier_id),
            ('Project', 'analytic_distribution_id', self._get_analytic_account_id)
            ]:
            value, error = method(data.get(field))
            if error:
                errors.append(error)
            else:
                validated[key] = value

        # Validate order lines
        order_line_errors = self._validate_order_lines(data.get('Details', []))
        if order_line_errors:
            errors.append(_("Order line errors: %s") % ", ".join(order_line_errors))

        return errors, validated

    def _create_purchase_order(self, data, validated):
        order_lines = self._prepare_order_lines(
            data.get('Details', []),
            self.env.user.company_id.pro_save_po_default_product_id.id
        )
        purchase_order = self.env['purchase.order'].create({
            'partner_id': validated['partner_id'],
            'user_id': validated['user_id'],
            'name': data['OrderNumber'],
            'analytic_distribution_id': validated['analytic_distribution_id'],
            'order_line': order_lines,
        })
        purchase_order.button_confirm()
        self.status = 'processed'
        self.purchase_order = purchase_order.id
        self.message_post(body=_("Purchase order created."))

    def _log_error(self, errors):
        """Log and post errors."""
        self.status = 'error'
        body = Markup('<ul>%s</ul>') % Markup().join(Markup('<li>%s</li>') % error for error in errors)
        self.message_post(body=body)

    def _validate_order_lines(self, details):
        errors = []
        for item in details:
            missing_fields = [field for field in ['EAN', 'Name', 'Qty', 'NetPrice'] if field not in item]
            if missing_fields:
                errors.append(f"Item missing required fields: {', '.join(missing_fields)}")
        return errors

    def _prepare_order_lines(self, details, default_product_id):
        order_lines = []
        for item in details:
            order_lines.append((0, 0, {
                'product_id': default_product_id,
                'name': f"[{item['EAN']}] {item['Name']}",
                'product_qty': float(item['Qty']),
                'price_unit': float(item['NetPrice']),
            }))
        return order_lines

    def _validate_order_data(self, data):
        required_fields = ['UserEmail', 'CVR', 'OrderNumber', 'Supplier', 'Project']
        missing_fields = [field for field in required_fields if field not in data]
        return missing_fields

    def _get_user_id(self, email):
        user = self.env['res.users'].search(
            [('email', '=ilike', email)], limit=1)
        return (user.id if user else None), (_("User not found.") if not user else '')

    def _get_supplier_id(self, supplier_name):
        supplier = self.env['res.partner'].search(
            [('pro_save_vendor_name', '=', supplier_name)], limit=1)
        return (supplier.id if supplier else None), (_("Supplier not found.") if not supplier else '')

    def _get_analytic_account_id(self, project_name):
        try:
            project_id = int(project_name)
        except (ValueError, TypeError):
            return None, _("Invalid project ID format: %s") % project_name

        analytic_account = self.env['account.analytic.account'].search(
            [('id', '=', project_id)], limit=1)
        return (analytic_account.id if analytic_account else None), (_("Analytic account for the project not found.") if not analytic_account else '')

    def _format_order_number(self, order_number):
        split_order_number = order_number.split("-")
        if len(split_order_number) != 3:
            return None, _("Invalid order number format: %s") % order_number

        formatted_order_number = f"PO{split_order_number[2]}"
        if self.env['purchase.order'].search_count([('name', '=', formatted_order_number)]):
            return None, _("Order number already exists.")

        return formatted_order_number, ''
