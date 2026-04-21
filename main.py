# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
import json
import logging
import pprint
from odoo import http, _
from odoo.http import request

_logger = logging.getLogger(__name__)


class FlytProSavePluginController(http.Controller):

    @http.route('/prosave/push/order', type="json", auth="pro_save_order", methods=['POST'])
    def prosave_push_order(self, **post):
        """Route to push orders into Odoo purchase orders from a third-party Prosave."""
        try:
            data = json.loads(request.httprequest.data)
            _logger.info("Received Prosave PO webhook data:\n%s", pprint.pformat(data))
            request.env['prosave.order.data'].create({
                'reference_id': request.env['ir.sequence'].next_by_code('prosave.order.data'),
                'status': 'to_process',
                'data':json.dumps(data, indent=4),
            })
            return {'status': 'success', 'message': 'Order data processed successfully.'}
        except Exception as e:
            _logger.error(e)
            request.env.cr.rollback()
            return self._error_response(_(e))
