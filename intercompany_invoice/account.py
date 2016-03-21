# -*- coding: utf-8 -*-
###############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2014-Today Julius Network Solutions SARL <contact@julius.fr>
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
###############################################################################

from openerp import models, api, fields, _
from openerp.exceptions import Warning

import logging
_logger = logging.getLogger(__name__)


class AccountInvoice(models.Model):
    _inherit = "account.invoice"
    
    customer_invoice_id = fields.Many2one('account.invoice',
                                          'Customer Invoice', readonly=True)
    supplier_invoice_id = fields.Many2one('account.invoice',
                                          'Supplier Invoice', readonly=True)
    
    
    @api.multi
    def compute_invoice_tax_lines(self):
        self.ensure_one()
        taxes_grouped = self.get_taxes_values()
        tax_lines = self.tax_line_ids.browse([])
        for tax in taxes_grouped.values():
            tax_lines += tax_lines.new(tax)

        self.tax_line_ids = tax_lines
            
        return []
    
    @api.one
    def copy(self, default=None):
        if default is None:
            default = {}
        default['supplier_invoice_id'] = False
        return super(AccountInvoice, self).copy(default)
    
    def copy_data(self, cr, uid, id, default=None, context=None):
        if context is None:
            context = {}
        if default is None:
            default = {}
        default['supplier_invoice_id'] = False
        return super(AccountInvoice, self).\
            copy_data(cr, uid, id, default=default, context=context)

    @api.multi
    def write(self, vals):

        res = super(AccountInvoice, self).write(vals)
        
        if self.user_has_groups('intercompany_invoice.group_intercompany_invoice'):
            res_company_obj = self.env['res.company']
            for ci in self:
                if self.type in ['out_invoice']:
                    company_ids = res_company_obj.sudo().search(
                        [('partner_id', '=', ci.partner_id.id)], limit=1)
                    _logger.debug("COMPANY %s " % company_ids)
                    if vals.get('state') == 'open' and \
                        company_ids and not ci.supplier_invoice_id:
                        self.customer_to_supplier()
        _logger.debug("SORTIE DU WRITE DE FACTURE")
        return res

    @api.multi
    def _check_intercompany_partner(self):
        res_company_obj = self.env['res.company']
        res_partner_obj = self.env['res.partner']

        company_id = res_company_obj.sudo().search(
            [('partner_id', '=', self.partner_id.id)], limit=1)

        partner_id = res_partner_obj.sudo().search(
            [('id', '=', self.company_id.partner_id.id),
            ('company_id', '=', False),], limit=1)
        
        if not partner_id:   
            Warning(_('Intercompany Partner not found !'))
        return company_id, partner_id
    
    
    @api.multi
    def get_position_and_payment_term(self, company, partner):
        prop = self.env['ir.property'].with_context(force_company=company.id)
        payment_term_id = prop.search([
                                ('company_id', '=', company.id),
                                ('name', '=', 'property_supplier_payment_term_id'),
                                ('res_id', '=', str('res.partner,%s' % partner.id)),
                                ], limit=1)
        if payment_term_id[0].value_reference:
            _logger.debug("payment_term_id : %s" % payment_term_id)
            payment_term_id = payment_term_id.value_reference.split(",")[1]
        else:
            payment_term_id = False
            
        fiscal_position_id = prop.search([
                                ('company_id', '=', company.id),
                                ('name', '=', 'property_account_position_id'),
                                ('res_id', '=', str('res.partner,%s' % partner.id)),
                                ], limit=1)
        
        if fiscal_position_id[0].value_reference :
            fiscal_position_id = fiscal_position_id.value_reference.split(",")[1]
        else:
            fiscal_position_id = False
        
        return fiscal_position_id, payment_term_id
        
    @api.multi
    def _get_vals_for_supplier_invoice(self, company, partner):
        """
        Prepare datas in the context of the other company
        """
        journal_obj = self.env['account.journal'].with_context(force_company=company.id)
        prop = self.env['ir.property'].with_context(force_company=company.id)
        
        journal = journal_obj.sudo().\
            search([
                    ('type', '=', 'purchase'),
                    ('company_id', '=', company.id),
#                     ('currency', '=', self.currency_id.id),
                    ], limit=1)
        if not journal:
            raise Warning(_('Impossible to generate the linked invoice to ' \
                            '%s, There is no purchase journal ' \
                            'defined.' %company.name))
        
        
        pay_account = partner.with_context(force_company=company.id).property_account_payable_id       

        if not pay_account:
            pay_account = self.env['ir.property'].with_context(force_company=company.id)\
                                .get('property_account_payable_id', 'res.partner')                         
                                        
        if partner.with_context(force_company=company.id).property_account_payable_id.company_id and \
            partner.with_context(force_company=company.id).property_account_payable_id.company_id.id != company.id:
            pay_dom = [
                       ('name', '=', 'property_account_payable'),
                       ('company_id', '=', company.id),
                       ]
            res_dom = [
                       ('res_id', '=', 'res.partner,%s' % partner.id),
                       ]
            pay_prop = prop.with_context(force_company=company.id).search(pay_dom + res_dom) \
                        or prop.with_context(force_company=company.id).search(pay_dom)
            if pay_prop:
                pay_account = pay_prop.with_context(force_company=company.id).get_by_record(pay_prop)
        
        
        fiscal_position_id, payment_term_id = self.get_position_and_payment_term(company, partner)
        
        res = {
            'state': 'draft',
            'partner_id': partner.id,
            'journal_id': journal.id,
            'account_id': pay_account.id,
            'company_id': company.id,
            'origin': self.name,
            'payment_term_id': payment_term_id,
            'fiscal_position_id': fiscal_position_id,
            'date_invoice': self.date_invoice or False,
            'date_due': self.date_due or False,
            'customer_invoice_id': self.id,
            'type': 'in_invoice',
        }
        
        return res
    
    @api.multi
    def _get_taxes(self, company, product_id, fiscal_position):
        self.ensure_one()
        fiscal_position_id = self.env['account.fiscal.position'].search([('id', '=', fiscal_position)])
        _logger.debug("USER %s" % self.env.uid)
        if product_id:
            product_id = self.env['product.product']\
                                .with_context(force_company=company.id).\
                                search([('id', '=', product_id.id)])
                                
            taxes_ids = product_id.supplier_taxes_id        
        
        my_taxes = taxes_ids.filtered(lambda r: r.company_id.id == company.id)    
        my_taxes = fiscal_position_id.map_tax(my_taxes)
        my_taxes = [t.id for t in my_taxes]
        
        if len(my_taxes) == 0 or not product_id :
            conf_id = self.env['account.config.settings'].search([('company_id', '=', company.id)])
            taxes_ids = [conf_id.default_purchase_tax_id.id]
        
        return my_taxes
    
    @api.multi
    def _get_account(self, fiscal_position, account_id):
        self.ensure_one()
        fiscal_position_id = self.env['account.fiscal.position'].search([('id', '=', fiscal_position)])
        account_id = fiscal_position_id.map_account(account_id)
        
        return account_id
    
    @api.multi
    def _get_vals_for_supplier_invoice_line(self, supplier_invoice, line, company, partner):  
        vals = {}
        vals.update({
                'invoice_id': supplier_invoice.id,
                'name': line.name,
                'quantity': line.quantity,
                'price_unit': line.price_unit,
                'uom_id': line.uom_id.id
            })
            
        prop = self.env['ir.property'].with_context(force_company=company.id)    
        if line.product_id:                                             
            account = prop.search([
                                ('company_id', '=', company.id),
                                ('name', '=', 'property_account_expense_id'),
                                ('res_id', '=', str('product.template,%s' % line.product_id.product_tmpl_id.id)),
                                ])
            
            if len(account) == 0:
                _logger.debug("Entree from product :" )   
                account = prop.search([
                                ('company_id', '=', company.id),
                                ('name', '=', 'property_account_expense_categ_id'),
                                ])
            
            account = account.value_reference.split(",")[1]
            vals.update( {
                    'product_id': line.product_id.id,
                    'account_id': account,
            })
            
        else:
            account = prop.search([
                                ('company_id', '=', company.id),
                                ('name', '=', 'property_account_expense_categ_id'),
                                ])
            account = account.value_reference.split(",")[1]            
            vals.update( {                
                'account_id': account,
            })
        
        fiscal_position_id, payment_term_id = self.get_position_and_payment_term(company, partner)
        taxes = self.sudo()._get_taxes(company, line.product_id, fiscal_position_id )
        if len(taxes) != 0:
            vals.update({'invoice_line_tax_ids': [(6, 0, taxes)],})
        
        if fiscal_position_id:
            new_account = self.sudo()._get_account(fiscal_position_id, vals['account_id'])
            vals.update({'account_id':new_account})
            
        _logger.debug("vals : %s" % vals)
        return vals

    @api.multi
    @api.depends('invoice_line')
    def customer_to_supplier(self):
        """
        This method will create the linked supplier invoice
        """

        AccountInvoice_obj = self.env['account.invoice']
        AccountInvoice_line_obj = self.env['account.invoice.line']

        for cust_invoice in self:
            if cust_invoice.supplier_invoice_id:
                raise Warning(_('You already had a supplier invoice '
                                'for this customer invoice.\n'
                                'Please delete the %s '
                                'if you want to create a new one')
                                % (cust_invoice.supplier_invoice_id.name))

            company, partner = self.sudo()._check_intercompany_partner()

            vals = self.sudo()._get_vals_for_supplier_invoice(company, partner)
            supplier_invoice = self.sudo().create(vals)
            
            self.write({'supplier_invoice_id': supplier_invoice.id})
            for line in cust_invoice.invoice_line_ids:
                vals = self.sudo().\
                    _get_vals_for_supplier_invoice_line(supplier_invoice, line,
                                                        company, partner)
                AccountInvoice_line_obj.sudo().create(vals)
            supplier_invoice.sudo().compute_invoice_tax_lines()
            
# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
