{%- set packages = salt['pillar.get']('install_packages', {}) %}
{%- set kernel = salt['grains.get']('kernel', '') %}
{%- if kernel == 'Linux' %}
{%- for package, version in packages.items() %}
{%- if version is not none %}
install_{{ package }}:
  pkg.installed:
    - name: '{{ package }}'
    - version: '{{ version }}'
{%- else %}
remove_{{ package }}:
  pkg.purged:
    - name: '{{ package }}'
{%- endif %}
{%- endfor %}
{%- elif kernel == 'Windows' %}
include:
  - .install_chocolatey

{%- for package, version in packages.items() %}
{%- if version is not none %}
install_{{ package }}:
  chocolatey.installed:
    - name: '{{ package }}'
    {%- if version != 'latest' %}
    - version: '{{ version }}'
    {%- endif %}
    - require:
      - module: install_chocolatey
{%- else %}
remove_{{ package }}:
  chocolatey.uninstalled:
    - name: '{{ package }}'
    - require:
      - module: install_chocolatey
{%- endif %}
{%- endfor %}
{%- endif %}
