{%- set kernel = salt['grains.get']('kernel', '') %}
{%- if kernel == 'Windows' %}
install_chocolatey:
  module.run:
    - name: chocolatey.bootstrap
    - order: 1
{%- endif %}
