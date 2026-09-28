FROM python:3.12-slim-bookworm

ENV LANG C.UTF-8

# Install system dependencies required by Odoo 20
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Build tools
    build-essential \
    # PostgreSQL client
    libpq-dev \
    postgresql-client \
    # lxml
    libxml2-dev \
    libxslt1-dev \
    # Pillow
    libjpeg-dev \
    libpng-dev \
    libfreetype6-dev \
    # LDAP
    libldap2-dev \
    libsasl2-dev \
    # OpenSSL
    libssl-dev \
    # Fonts for PDF rendering
    fonts-dejavu-core \
    fonts-inconsolata \
    fonts-font-awesome \
    fonts-roboto-unhinted \
    gsfonts \
    # Node / rtlcss
    npm \
    # wkhtmltopdf for PDF reports
    wkhtmltopdf \
    # Misc
    git \
    curl \
    && npm install -g rtlcss \
    && rm -rf /var/lib/apt/lists/*

# Copy Odoo source into the image
COPY ./odoo /opt/odoo

# Install Python dependencies from the repo's requirements.txt
RUN pip install --no-cache-dir -r /opt/odoo/requirements.txt

# Install extra Python dependencies for custom addons
COPY ./requirements.txt /opt/extra-requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends \
    pkg-config libcairo2-dev \
    && pip install --no-cache-dir -r /opt/extra-requirements.txt \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Create the odoo user and required directories
RUN useradd -ms /bin/bash odoo \
    && mkdir -p /var/lib/odoo /mnt/enterprise-addons /mnt/extra-addons \
    && chown -R odoo:odoo /var/lib/odoo /mnt/enterprise-addons /mnt/extra-addons /opt/odoo

USER odoo

EXPOSE 8069 8071 8072

ENTRYPOINT ["/opt/odoo/odoo-bin"]
CMD ["--config=/etc/odoo/odoo.conf"]
