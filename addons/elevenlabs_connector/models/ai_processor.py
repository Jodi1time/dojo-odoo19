# -*- coding: utf-8 -*-

import logging
import json
import requests
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AIProcessor(models.AbstractModel):
    _name = 'ai.processor'
    _description = 'AI Processor Abstraction'

    def _get_provider(self):
        """Get configured AI provider"""
        return self.env['ir.config_parameter'].sudo().get_str(
            'elevenlabs_connector.ai_provider', 'openai'
        )


    def process_query(self, transcribed_text, context=None):
        """
        Process a voice query via direct API calls (bypasses Odoo AI provider to avoid encoding issues)
        
        IMPORTANT: This method NEVER calls Odoo's ai.provider model.
        All AI processing uses direct HTTP calls to external APIs (OpenAI/Gemini).
        This completely avoids the latin-1 encoding issue in Odoo's AI stack.
        
        Args:
            transcribed_text: Text from speech-to-text
            context: Additional context (user, conversation history, etc.)
        
        Returns:
            str: AI-generated response text (sanitized to ASCII)
        """
        if context is None:
            context = {}
        
        # Log that we're using direct API calls (not Odoo AI)
        _logger.info('Processing AI query via DIRECT API calls (NOT using Odoo ai.provider)')
        
        # Get configured provider - we ONLY use direct API calls, never Odoo's AI provider
        # This completely bypasses the latin-1 encoding issue in Odoo's AI stack
        provider = self._get_provider()
        _logger.info('AI provider configured: %s', provider)
        
        # Check API keys are configured
        if provider in ('openai', 'odoo_native'):
            openai_key = self.env['ir.config_parameter'].sudo().get_str('openai.api_key') or \
                        self.env['ir.config_parameter'].sudo().get_str('elevenlabs_connector.openai_api_key')
            if not openai_key:
                _logger.error('OpenAI API key not configured for provider: %s', provider)
            else:
                _logger.debug('OpenAI API key found (length: %d)', len(openai_key))
        elif provider == 'gemini':
            gemini_key = self.env['ir.config_parameter'].sudo().get_str('gemini.api_key') or \
                        self.env['ir.config_parameter'].sudo().get_str('elevenlabs_connector.gemini_api_key')
            if not gemini_key:
                _logger.error('Gemini API key not configured for provider: %s', provider)
        else:
                _logger.debug('Gemini API key found (length: %d)', len(gemini_key))
        
        # Get database context for common queries
        db_context = self._get_database_context(transcribed_text)
        
        # Enhanced system prompt with database context
        system_prompt = """You are a helpful AI assistant integrated with Odoo ERP. Answer questions clearly and concisely.
        
When answering questions about Odoo data, use ONLY the actual data provided below. Do not guess or make up numbers.
If the data shows a specific count or value, use that exact value in your response.

Database Context:
{db_context}
""".format(db_context=db_context if db_context else "No specific database queries detected.")
        
        # Route to appropriate provider (all use direct HTTP calls, no Odoo AI stack)
        # IMPORTANT: None of these methods call Odoo's ai.provider
        # NOTE: Responses are already sanitized inside _process_openai/_process_gemini
        # We sanitize again here as a safety measure
        try:
            if provider == 'openai':
                # Response is already sanitized in _process_openai, but sanitize again for safety
                response = self._process_openai(transcribed_text, system_prompt, context)
                return self._sanitize_text(response)  # Double sanitization for safety
            elif provider == 'gemini':
                # Response is already sanitized in _process_gemini, but sanitize again for safety
                response = self._process_gemini(transcribed_text, system_prompt, context)
                return self._sanitize_text(response)  # Double sanitization for safety
            elif provider == 'odoo_native':
                # Even "odoo_native" now uses direct API calls to avoid latin-1 issues
                # User can configure OpenAI/Gemini API keys and we'll use those directly
                _logger.info('Odoo Native AI selected, but using direct OpenAI API calls to avoid encoding issues')
                response = self._process_openai(transcribed_text, system_prompt, context)
                return self._sanitize_text(response)  # Double sanitization for safety
            elif provider == 'custom':
                response = self._process_custom(transcribed_text, system_prompt, context)
                return self._sanitize_text(response)
            else:
                raise UserError(f'Unsupported AI provider: {provider}. Please configure an AI provider in Settings.')
        except UserError as ue:
            # UserError already has a message - just re-raise it (it's already sanitized)
            raise
        except Exception as e:
            # Log the error and raise a sanitized UserError
            _logger.error('Unexpected error in process_query: %s', str(e), exc_info=True)
            error_type = type(e).__name__

            # SPECIAL CASE: If the underlying error mentions latin-1 / UnicodeEncodeError,
            # hide the low-level details from the end user and show a generic message.
            error_str = str(e) if e else ''
            if 'latin-1' in error_str or 'UnicodeEncodeError' in error_type:
                raise UserError(
                    'AI processing failed due to an encoding issue. '
                    'Please try a shorter or simpler question, or contact your administrator.'
                )

            try:
                # Try to get a safe error message
                safe_msg = self._sanitize_text(error_str)
                if safe_msg and len(safe_msg) > 0:
                    raise UserError(f'AI processing error ({error_type}): {safe_msg}')
                else:
                    raise UserError(f'AI processing error: {error_type}. Please check your AI settings.')
            except UserError:
                # Re-raise UserError as-is
                raise
            except Exception:
                # If even sanitization fails, raise a generic error
                raise UserError(f'AI processing failed ({error_type}). Please check your AI settings and try again.')


    def _process_openai(self, transcribed_text, system_prompt, context):
        """
        Process query using OpenAI - API determines model automatically.
        """
        _logger.info('Processing OpenAI query - letting API determine model')
        
        api_key = self.env['ir.config_parameter'].sudo().get_str('openai.api_key') or \
                  self.env['ir.config_parameter'].sudo().get_str('elevenlabs_connector.openai_api_key')
        
        if not api_key:
            _logger.error('OpenAI API key not found in config parameters')
            raise UserError('OpenAI API key not configured. Please set it in Settings -> Integrations -> ElevenLabs Voice Connector -> OpenAI API Key field.')
        
        # First, get available models from OpenAI API
        models_url = 'https://api.openai.com/v1/models'
        headers = {
            'Authorization': f'Bearer {api_key}',
        }
        
        try:
            _logger.info('Fetching available models from OpenAI API')
            models_response = requests.get(models_url, headers=headers, timeout=10)
            models_response.raise_for_status()
            models_data = json.loads(models_response.text)
            
            # Find first chat-compatible model (usually gpt-3.5-turbo or gpt-4)
            available_model = None
            if 'data' in models_data:
                for model in models_data['data']:
                    model_id = model.get('id', '')
                    # Prefer chat models
                    if 'gpt' in model_id.lower() and ('turbo' in model_id.lower() or 'gpt-4' in model_id.lower()):
                        available_model = model_id
                        break
                # If no chat model found, use first available
                if not available_model and models_data['data']:
                    available_model = models_data['data'][0].get('id')
            
            if not available_model:
                # Fallback: use a common model name (but let API validate)
                available_model = 'gpt-3.5-turbo'
            
            _logger.info('Using OpenAI model determined by API: %s', available_model)
        except Exception as e:
            _logger.warning('Could not fetch models from OpenAI, using default: %s', str(e))
            # If model listing fails, let OpenAI API handle it with a common model
            available_model = 'gpt-3.5-turbo'
        
        url = 'https://api.openai.com/v1/chat/completions'
        
        # Build full text (system prompt + user text)
        full_text = f"{system_prompt}\n\nUser: {transcribed_text}"
        
        # Use model determined by API - no hardcoding
        payload = {
            'model': available_model,
            'messages': [
                {'role': 'user', 'content': full_text}
            ],
            'temperature': 0.7,
            'max_tokens': 1000,
        }
        
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json; charset=utf-8',
        }
        
        try:
            # Ensure UTF-8 encoding for the request
            json_data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            
            session = requests.Session()
            response = session.post(
                url,
                headers=headers,
                data=json_data,
                timeout=30
            )
            
            # Force UTF-8 encoding before any text operations
            if response.encoding is None or response.encoding.lower() in ('iso-8859-1', 'latin-1'):
                response.encoding = 'utf-8'
            
            response.raise_for_status()
            
            # Parse JSON with explicit UTF-8 handling
            result = json.loads(response.text)
            
            if 'choices' in result and len(result['choices']) > 0:
                # CRITICAL: Sanitize response IMMEDIATELY to avoid latin-1 encoding issues
                raw_content = result['choices'][0]['message']['content']
                # Sanitize before returning - this prevents any latin-1 encoding errors
                sanitized_content = self._sanitize_text(raw_content)
                _logger.info('OpenAI response sanitized: %d chars -> %d chars', len(raw_content), len(sanitized_content))
                return sanitized_content
            else:
                raise UserError('Unexpected response format from OpenAI API')
                
        except requests.exceptions.RequestException as e:
            _logger.error('OpenAI API request failed: %s', str(e), exc_info=True)
            # Create ASCII-safe error message (never use latin-1)
            error_msg = 'OpenAI API request failed'
            if hasattr(e, 'response') and e.response is not None:
                try:
                    # Ensure UTF-8 encoding for error response
                    if e.response.encoding is None or e.response.encoding.lower() in ('iso-8859-1', 'latin-1'):
                        e.response.encoding = 'utf-8'
                    error_detail = json.loads(e.response.text)
                    raw_error = error_detail.get('error', {}).get('message', '')
                    if raw_error:
                        # Sanitize error message to ASCII
                        error_msg = self._sanitize_text(raw_error) or f'HTTP {e.response.status_code} error'
                    else:
                        error_msg = f'HTTP {e.response.status_code} error'
                except Exception as parse_error:
                    _logger.warning('Could not parse OpenAI error response: %s', str(parse_error))
                    # Ensure UTF-8 for text extraction
                    if e.response.encoding is None or e.response.encoding.lower() in ('iso-8859-1', 'latin-1'):
                        e.response.encoding = 'utf-8'
                    status_code = e.response.status_code if hasattr(e.response, 'status_code') else 'unknown'
                    error_msg = f'HTTP {status_code} error'
            else:
                # Network error
                error_type = type(e).__name__
                if 'timeout' in str(e).lower():
                    error_msg = 'Request timeout - please try again'
                elif 'connection' in str(e).lower():
                    error_msg = 'Connection error - check your internet connection'
                else:
                    error_msg = f'Network error: {error_type}'
            # Raise with ASCII-safe message only
            raise UserError(f'OpenAI API error: {error_msg}')

    def _process_gemini(self, transcribed_text, system_prompt, context):
        """
        Process query using Google Gemini - API determines model automatically via ListModels.
        """
        _logger.info('Processing Gemini query - letting API determine model via ListModels')
        
        api_key = self.env['ir.config_parameter'].sudo().get_str('gemini.api_key') or \
                  self.env['ir.config_parameter'].sudo().get_str('elevenlabs_connector.gemini_api_key')
        
        if not api_key:
            _logger.error('Gemini API key not found in config parameters')
            raise UserError('Gemini API key not configured. Please set it in Settings -> Integrations -> ElevenLabs Voice Connector -> Gemini API Key field.')
        
        # First, get available models from Gemini API
        list_models_url = 'https://generativelanguage.googleapis.com/v1beta/models'
        
        try:
            _logger.info('Fetching available models from Gemini API')
            models_response = requests.get(
                list_models_url,
                params={'key': api_key},
                timeout=10
            )
            models_response.raise_for_status()
            
            if models_response.encoding is None or models_response.encoding.lower() in ('iso-8859-1', 'latin-1'):
                models_response.encoding = 'utf-8'
            
            models_data = json.loads(models_response.text)
            
            # Find first model that supports generateContent
            # Prefer stable models over experimental ones (avoid -exp suffix)
            # Also prefer common models like gemini-pro, gemini-flash-latest which have better free tier quotas
            available_model = None
            preferred_models = ['gemini-pro', 'gemini-flash-latest', 'gemini-1.5-pro']
            
            if 'models' in models_data:
                # First pass: Look for preferred stable models
                for preferred in preferred_models:
                    for model in models_data['models']:
                        model_name = model.get('name', '')
                        supported_methods = model.get('supportedGenerationMethods', [])
                        # Check if this is the preferred model and supports generateContent
                        if (preferred in model_name.lower() and 
                            'generateContent' in supported_methods and
                            '-exp' not in model_name.lower()):  # Skip experimental models
                            if model_name.startswith('models/'):
                                available_model = model_name.replace('models/', '')
                            else:
                                available_model = model_name
                            _logger.info('Found preferred Gemini model: %s', available_model)
                            break
                    if available_model:
                        break
                
                # Second pass: If no preferred model found, use any stable (non-experimental) model
                if not available_model:
                    for model in models_data['models']:
                        model_name = model.get('name', '')
                        supported_methods = model.get('supportedGenerationMethods', [])
                        # Skip experimental models (they have stricter quotas)
                        if ('generateContent' in supported_methods and 
                            '-exp' not in model_name.lower() and
                            'experimental' not in model_name.lower()):
                            if model_name.startswith('models/'):
                                available_model = model_name.replace('models/', '')
                            else:
                                available_model = model_name
                            _logger.info('Found stable Gemini model with generateContent support: %s', available_model)
                            break
            
            if not available_model:
                raise UserError('No Gemini models found that support generateContent. Please check your API key and account access.')
            
            _logger.info('Using Gemini model determined by API: %s', available_model)
        except requests.exceptions.RequestException as e:
            _logger.error('Failed to fetch Gemini models: %s', str(e))
            raise UserError(f'Failed to get available Gemini models. Please check your API key: {str(e)}')
        except Exception as e:
            _logger.error('Error processing Gemini models list: %s', str(e))
            raise UserError(f'Error determining Gemini model: {str(e)}')
        
        # Build full text (system prompt + user text)
        full_text = f"{system_prompt}\n\nUser: {transcribed_text}"
        
        # Use model determined by API - no hardcoding
        url = f'https://generativelanguage.googleapis.com/v1beta/models/{available_model}:generateContent'
        
        payload = {
            'contents': [{
                'parts': [{
                    'text': full_text
                }]
            }]
        }

        headers = {
            'Content-Type': 'application/json; charset=utf-8',
        }

        try:
            # Encode JSON manually as UTF-8 to avoid any latin-1 issues
            json_data = json.dumps(payload, ensure_ascii=False).encode('utf-8')

            _logger.info('Sending request to Gemini API with API-determined model: %s (text length: %d)', available_model, len(full_text))

            session = requests.Session()
            response = session.post(
                url,
                headers=headers,
                data=json_data,
                params={'key': api_key},
                timeout=30
            )

            # Force UTF-8 encoding before any text operations
            if response.encoding is None or response.encoding.lower() in ('iso-8859-1', 'latin-1'):
                response.encoding = 'utf-8'

            _logger.info('Gemini API response status: %s', response.status_code)
            response.raise_for_status()

            # Parse JSON with explicit UTF-8 handling
            result = json.loads(response.text)

            if 'candidates' in result and len(result['candidates']) > 0:
                candidate = result['candidates'][0]
                if 'content' in candidate and 'parts' in candidate['content']:
                    # CRITICAL: Sanitize response IMMEDIATELY to avoid latin-1 encoding issues
                    raw_content = candidate['content']['parts'][0].get('text', '')
                    # Sanitize before returning - this prevents any latin-1 encoding errors
                    sanitized_content = self._sanitize_text(raw_content)
                    _logger.info('Gemini response sanitized: %d chars -> %d chars', len(raw_content), len(sanitized_content))
                    return sanitized_content
                else:
                    raise UserError('Unexpected response format from Gemini API')
            else:
                raise UserError('No response from Gemini API')

        except requests.exceptions.RequestException as e:
            _logger.error('Gemini API request failed: %s', str(e), exc_info=True)
            # Create ASCII-safe error message (never use latin-1)
            error_msg = 'Gemini API request failed'
            if hasattr(e, 'response') and e.response is not None:
                try:
                    # Ensure UTF-8 encoding for error response
                    if e.response.encoding is None or e.response.encoding.lower() in ('iso-8859-1', 'latin-1'):
                        e.response.encoding = 'utf-8'
                    error_detail = json.loads(e.response.text)
                    raw_error = error_detail.get('error', {}).get('message', '')
                    if raw_error:
                        # Sanitize error message to ASCII
                        error_msg = self._sanitize_text(raw_error) or f'HTTP {e.response.status_code} error'
                    else:
                        error_msg = f'HTTP {e.response.status_code} error'
                except Exception as parse_error:
                    _logger.warning('Could not parse Gemini error response: %s', str(parse_error))
                    # Ensure UTF-8 for text extraction
                    if e.response.encoding is None or e.response.encoding.lower() in ('iso-8859-1', 'latin-1'):
                        e.response.encoding = 'utf-8'
                    status_code = e.response.status_code if hasattr(e.response, 'status_code') else 'unknown'
                    error_msg = f'HTTP {status_code} error'
            else:
                # Network error
                error_type = type(e).__name__
                if 'timeout' in str(e).lower():
                    error_msg = 'Request timeout - please try again'
                elif 'connection' in str(e).lower():
                    error_msg = 'Connection error - check your internet connection'
                else:
                    error_msg = f'Network error: {error_type}'
            # Raise with ASCII-safe message only
            raise UserError(f'Gemini API error: {error_msg}')

    def _process_odoo_native(self, transcribed_text, system_prompt, context):
        """Process query - redirects to OpenAI direct API to avoid Odoo AI encoding issues"""
        # Odoo Native AI has latin-1 encoding issues, so we redirect to direct OpenAI calls
        # User should configure OpenAI API key in settings
        _logger.info('Odoo Native AI selected, but using direct OpenAI API to avoid encoding issues')
        return self._process_openai(transcribed_text, system_prompt, context)

    def _process_custom(self, transcribed_text, system_prompt, context):
        """Process query using custom provider (extensible hook)"""
        # This is a hook for custom implementations
        # Other modules can extend this method
        raise UserError('Custom AI provider not implemented. Please extend this method in a custom module.')

    def _get_database_context(self, query_text):
        """
        Query Odoo database based on the user's question to provide real data to the AI.
        Supports querying any Odoo model dynamically.
        Returns a string with actual database information.
        """
        context_parts = []
        query_lower = query_text.lower()
        
        try:
            # Model mappings: keyword -> (model_name, domain, description)
            model_mappings = [
                # Employees
                (['employee', 'employees', 'staff', 'workers', 'people'], 
                 'hr.employee', [('active', '=', True)], 'Active Employees'),
                
                # Contacts/Partners (all contacts, not just customers)
                (['contact', 'contacts', 'partner', 'partners', 'people', 'person', 'persons'], 
                 'res.partner', [('active', '=', True)], 'Active Contacts'),
                
                # Customers (specifically customers)
                (['customer', 'customers', 'client', 'clients'], 
                 'res.partner', [('customer_rank', '>', 0), ('active', '=', True)], 'Active Customers'),
                
                # Vendors/Suppliers
                (['vendor', 'vendors', 'supplier', 'suppliers'], 
                 'res.partner', [('supplier_rank', '>', 0), ('active', '=', True)], 'Active Vendors'),
                
                # CRM Opportunities
                (['opportunity', 'opportunities', 'lead', 'leads', 'crm', 'pipeline'], 
                 'crm.lead', [('type', '=', 'opportunity'), ('active', '=', True)], 'Active Opportunities'),
                
                # Products
                (['product', 'products', 'item', 'items'], 
                 'product.product', [('active', '=', True)], 'Active Products'),
                
                # Product Templates
                (['product template', 'product templates'], 
                 'product.template', [('active', '=', True)], 'Active Product Templates'),
                
                # Sales Orders
                (['sales order', 'sales orders', 'quotation', 'quotations', 'so', 'sale order'], 
                 'sale.order', [('state', 'in', ['draft', 'sent', 'sale'])], 'Sales Orders'),
                
                # Invoices
                (['invoice', 'invoices', 'billing'], 
                 'account.move', [('move_type', 'in', ['out_invoice', 'out_refund']), ('state', '!=', 'cancel')], 'Invoices'),
                
                # Purchase Orders
                (['purchase order', 'purchase orders', 'po', 'purchases'], 
                 'purchase.order', [('state', 'in', ['draft', 'sent', 'to approve', 'purchase'])], 'Purchase Orders'),
                
                # Projects
                (['project', 'projects'], 
                 'project.project', [('active', '=', True)], 'Active Projects'),
                
                # Tasks
                (['task', 'tasks', 'todo', 'todos'], 
                 'project.task', [('active', '=', True)], 'Active Tasks'),
                
                # Companies
                (['company', 'companies'], 
                 'res.company', [], 'Companies'),
                
                # Users
                (['user', 'users'], 
                 'res.users', [('active', '=', True)], 'Active Users'),
                
                # Departments
                (['department', 'departments'], 
                 'hr.department', [('active', '=', True)], 'Active Departments'),
            ]
            
            # Find matching models based on keywords
            matched_models = []
            for keywords, model_name, domain, description in model_mappings:
                if any(keyword in query_lower for keyword in keywords):
                    try:
                        # Check if model exists
                        model = self.env.get(model_name)
                        if model is None:
                            continue
                        
                        # Query the model
                        count = model.search_count(domain)
                        matched_models.append((description, count, model_name, domain))
                    except Exception as e:
                        _logger.debug('Model %s not available or error: %s', model_name, str(e))
                        continue
            
            # Add counts for matched models
            for description, count, model_name, domain in matched_models:
                context_parts.append(f"{description}: {count}")
            
            # Special handling for detailed queries
            # Employees by department
            if any(keyword in query_lower for keyword in ['employee', 'employees']) and 'department' in query_lower:
                try:
                    departments = self.env['hr.department'].search([('active', '=', True)])
                    dept_info = []
                    for dept in departments:
                        dept_count = self.env['hr.employee'].search_count([
                            ('department_id', '=', dept.id),
                            ('active', '=', True)
                        ])
                        if dept_count > 0:
                            dept_info.append(f"{dept.name}: {dept_count} employees")
                    if dept_info:
                        context_parts.append("Employees by Department: " + "; ".join(dept_info))
                except Exception:
                    pass
            
            # Opportunities by stage
            if any(keyword in query_lower for keyword in ['opportunity', 'opportunities']) and ('stage' in query_lower or 'pipeline' in query_lower):
                try:
                    stages = self.env['crm.stage'].search([])
                    stage_info = []
                    for stage in stages:
                        stage_count = self.env['crm.lead'].search_count([
                            ('stage_id', '=', stage.id),
                            ('type', '=', 'opportunity'),
                            ('active', '=', True)
                        ])
                        if stage_count > 0:
                            stage_info.append(f"{stage.name}: {stage_count} opportunities")
                    if stage_info:
                        context_parts.append("Opportunities by Stage: " + "; ".join(stage_info))
                except Exception:
                    pass
            
            # If no specific matches, try to query common models that might be relevant
            if not context_parts:
                # Try generic queries for common models
                common_queries = [
                    ('res.partner', [('active', '=', True)], 'Total Contacts'),
                    ('hr.employee', [('active', '=', True)], 'Total Employees'),
                ]
                
                for model_name, domain, description in common_queries:
                    try:
                        model = self.env.get(model_name)
                        if model is not None:
                            count = model.search_count(domain)
                            if count > 0:
                                context_parts.append(f"{description}: {count}")
                    except Exception:
                        continue
            
        except Exception as e:
            _logger.warning('Error querying database context: %s', str(e), exc_info=True)
            # Don't fail if database query fails, just continue without context
        
        return "\n".join(context_parts) if context_parts else None
    
    def _sanitize_text(self, text):
        """
        Sanitize AI output text to avoid encoding issues.
        - Replace problematic unicode characters with ASCII equivalents.
        - NEVER use latin-1 encoding - only use UTF-8 or ASCII.
        """
        if not text:
            return text

        # Convert to string if needed
        if not isinstance(text, str):
            text = str(text)

        # Mapping of common problematic characters to ASCII
        replacements = {
            '→': '->',
            '←': '<-',
            '⇒': '=>',
            '⇐': '<=',
            '"': '"',
            '"': '"',
            ''': "'",
            ''': "'",
            '…': '...',
            '—': '--',
            '–': '-',
        }

        for bad, good in replacements.items():
            text = text.replace(bad, good)

        # Use ASCII encoding only (never latin-1) - this is safe for Odoo
        try:
            # Encode to ASCII, ignoring any characters that can't be encoded
            text = text.encode('ascii', errors='ignore').decode('ascii')
        except Exception:
            # Last resort: return empty string if encoding completely fails
            _logger.warning('Failed to sanitize text, returning empty string')
            return ''

        return text

