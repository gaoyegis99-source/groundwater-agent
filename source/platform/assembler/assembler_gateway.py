from __future__ import annotations

import re

from assembler_common import (
    KERNEL_ROUTE_PREFIXES, ROUTE_FIELDS, ModuleContract, RouteBinding,
    ROUTE_PREFIX_PATTERN, ServiceBinding, PROJECT_UPLOAD_BYTES, require_fields,
    require_string,
)

def _compile_routes(
    value: object,
    services: dict[str, ServiceBinding],
    modules: dict[str, ModuleContract],
) -> list[RouteBinding]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("PRODUCT_ROUTES_INVALID")
    routes: list[RouteBinding] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("PRODUCT_ROUTES_INVALID")
        require_fields(item, ROUTE_FIELDS, "PRODUCT_ROUTES_INVALID")
        prefix, service, contract_id = (
            require_string(item["prefix"], "PRODUCT_ROUTES_INVALID"),
            item["service"],
            item["contract"],
        )
        segments = prefix.split("/")
        if (
            not ROUTE_PREFIX_PATTERN.fullmatch(prefix)
            or prefix.endswith("/")
            or "//" in prefix
            or any(segment in {".", ".."} for segment in segments)
        ):
            raise ValueError("PRODUCT_ROUTE_PREFIX_INVALID")
        if (
            service not in services
            or contract_id not in modules[service].http_contracts
        ):
            raise ValueError("PRODUCT_ROUTE_UNSUPPORTED")
        if any(
            prefix == reserved
            or prefix.startswith(reserved + "/")
            or reserved.startswith(prefix + "/")
            for reserved in KERNEL_ROUTE_PREFIXES
        ):
            raise ValueError("PRODUCT_ROUTE_CONFLICT")
        routes.append(RouteBinding(
            prefix=prefix,
            service=service,
            contract=contract_id,
            base_path=modules[service].http_contracts[contract_id].base_path,
        ))
    prefixes = [item.prefix for item in routes]
    if len(prefixes) != len(set(prefixes)):
        raise ValueError("PRODUCT_ROUTE_CONFLICT")
    return routes

def _nginx_config(
    product_id: str,
    routes: list[RouteBinding],
    services: dict[str, ServiceBinding],
) -> str:
    service_variables = {
        name: "$upstream_" + re.sub(r"[^a-z0-9_]", "_", name.lower())
        for name in {route.service for route in routes}
    }
    forwarded_headers = (
        " proxy_pass_request_headers off;"
        " proxy_hide_header X-Request-ID;"
        " proxy_set_header Host $host;"
        " proxy_set_header Content-Type $content_type;"
        " proxy_set_header Content-Length $content_length;"
        " proxy_set_header Accept $http_accept;"
        " proxy_set_header Last-Event-ID $http_last_event_id;"
        " proxy_set_header X-Request-ID $request_id;"
        " proxy_set_header X-Trace-ID $request_id;"
        " proxy_set_header traceparent $http_traceparent;"
        " proxy_set_header X-Project-ID $verified_project;"
        " proxy_set_header X-Client-Type web;"
        " proxy_set_header X-Platform-Subject $verified_subject;"
        " proxy_set_header X-Platform-Display-Name $verified_display_name;"
        " proxy_set_header X-Platform-Roles $verified_roles;"
        " proxy_set_header X-Content-SHA256 $http_x_content_sha256;"
        " proxy_set_header Idempotency-Key $http_idempotency_key;"
        " proxy_set_header Range $http_range;"
    )
    kernel_auth_headers = (
        " proxy_set_header Cookie $http_cookie;"
        " proxy_set_header Authorization $http_authorization;"
        " proxy_set_header X-CSRF-Token $http_x_csrf_token;"
    )
    locations = []
    for route in sorted(routes, key=lambda item: len(item.prefix), reverse=True):
        resource_options = (
            f" client_max_body_size {PROJECT_UPLOAD_BYTES}; proxy_request_buffering off;"
            " proxy_buffering off; proxy_http_version 1.1;"
            " proxy_read_timeout 1h; proxy_send_timeout 60s; client_body_timeout 60s;"
        ) if route.service == "data" else ""
        locations.append(
            f"        location {route.prefix} {{"
            f" rewrite ^{re.escape(route.prefix)}(.*)$ {route.base_path}$1 break;"
            f" proxy_pass {service_variables[route.service]};"
            f"{forwarded_headers}{resource_options} }}"
        )
    locations.extend(
        [
            "        location /api/v11/ { auth_request off; default_type application/problem+json; return 410 '{\"type\":\"urn:platform:problem:API_VERSION_RETIRED\",\"title\":\"API version retired\",\"status\":410,\"detail\":\"Public API v11 has been replaced by v12.\",\"code\":\"API_VERSION_RETIRED\"}'; }",
            "        location = /api/v12/health { auth_request off; proxy_pass $upstream_kernel; }",
            "        location = /api/v12/auth/login { auth_request off; proxy_pass $upstream_kernel; proxy_set_header Host $host; proxy_set_header X-Request-ID $request_id; proxy_set_header X-Trace-ID $request_id; }",
            "        location = /data { return 308 /data/; }",
            f"        location ^~ /data/v3/ {{ proxy_pass $upstream_data;{forwarded_headers} client_max_body_size {PROJECT_UPLOAD_BYTES}; proxy_request_buffering off; proxy_read_timeout 1h; }}",
            "        location ^~ /data/internal { return 404; }",
            "        location ^~ /data/ { try_files $uri $uri/ /data/index.html; }",
            "        location = /knowledge { return 308 /knowledge/; }",
            f"        location ^~ /knowledge/v3/ {{ proxy_pass $upstream_knowledge;{forwarded_headers} client_max_body_size 16777216; }}",
            "        location ^~ /knowledge/internal { return 404; }",
            "        location ^~ /knowledge/ { try_files $uri $uri/ /knowledge/index.html; }",
            '        location = /product-ui.json { auth_request off; add_header Cache-Control "no-store" always; add_header Content-Security-Policy "default-src \'none\'; frame-ancestors \'none\'; base-uri \'none\'" always; add_header X-Content-Type-Options nosniff always; add_header Referrer-Policy no-referrer always; add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always; try_files /product-ui.json =404; }',
            '        location = /product-icon.svg { auth_request off; add_header Cache-Control "no-store" always; add_header Content-Security-Policy "default-src \'none\'; frame-ancestors \'none\'; base-uri \'none\'" always; add_header X-Content-Type-Options nosniff always; add_header Referrer-Policy no-referrer always; add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always; try_files /product-icon.svg =404; }',
            "        location = / { auth_request off; expires -1; try_files /index.html =404; }",
            "        location /assets/ { auth_request off; try_files $uri =404; }",
            "        location / { auth_request off; return 404; }",
            "        location /api/v12/ {"
            " proxy_pass $upstream_kernel; proxy_read_timeout 1h;"
            f"{forwarded_headers}{kernel_auth_headers} }}",
            "        location ~ ^/api/v12/sessions/.*/events$ {"
            " proxy_pass $upstream_kernel;"
            " proxy_buffering off; proxy_cache off; proxy_read_timeout 1h;"
            f"{forwarded_headers}{kernel_auth_headers} }}",
        ]
    )
    return (
        "events {}\nhttp {\n"
        "    server_tokens off;\n"
        "    include /etc/nginx/mime.types;\n"
        "    resolver 127.0.0.11 valid=5s ipv6=off;\n"
        "    resolver_timeout 2s;\n"
        "    proxy_connect_timeout 2s;\n"
        "    default_type application/octet-stream;\n"
        "    log_format platform '$request_id $status $request_method $uri request_time=$request_time upstream_time=$upstream_response_time subject=$verified_subject bytes_in=$request_length bytes_out=$bytes_sent';\n"
        "    access_log /dev/stdout platform;\n"
        "    error_log /dev/stderr warn;\n"
        "    server {\n        listen 8080;\n"
        "        root /usr/share/nginx/html;\n"
        "        set $upstream_kernel http://kernel-service:8000;\n"
        "        set $upstream_data http://data-service:8000;\n"
        "        set $upstream_knowledge http://knowledge-next-service:8000;\n"
        + "".join(
            f"        set {variable} {services[name].internal_base_url};\n"
            for name, variable in sorted(service_variables.items())
        )
        + "        add_header X-Request-ID $request_id always;\n"
        "        add_header Content-Security-Policy \"default-src 'self'; connect-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'; worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'\" always;\n"
        "        add_header X-Content-Type-Options nosniff always;\n"
        "        add_header Referrer-Policy no-referrer always;\n"
        "        add_header Strict-Transport-Security \"max-age=31536000; includeSubDomains\" always;\n"
        "        auth_request /_platform_identity;\n"
        "        auth_request_set $verified_project $upstream_http_x_project_id;\n"
        "        auth_request_set $verified_subject $upstream_http_x_platform_subject;\n"
        "        auth_request_set $verified_display_name $upstream_http_x_platform_display_name;\n"
        "        auth_request_set $verified_roles $upstream_http_x_platform_roles;\n"
        "        error_page 401 = @platform_unauthorized;\n"
        "        error_page 403 = @platform_forbidden;\n"
        "        location = /_platform_identity { internal; auth_request off;"
        f" client_max_body_size {PROJECT_UPLOAD_BYTES};"
        " proxy_pass $upstream_kernel/internal/identity/verify;"
        " proxy_pass_request_body off; proxy_pass_request_headers off;"
        " proxy_set_header Content-Length \"\";"
        " proxy_set_header X-Request-ID $request_id;"
        " proxy_set_header X-Trace-ID $request_id;"
        " proxy_set_header traceparent $http_traceparent;"
        " proxy_set_header Cookie $http_cookie;"
        " proxy_set_header Authorization $http_authorization;"
        " proxy_set_header X-CSRF-Token $http_x_csrf_token;"
        " proxy_set_header X-Original-Method $request_method; }\n"
        "        location @platform_unauthorized { auth_request off; default_type application/problem+json; return 401 '{\"type\":\"urn:"
        + product_id
        + ":problem:AUTHENTICATION_REQUIRED\",\"title\":\"Authentication required\",\"status\":401,\"detail\":\"A local research login or API token is required.\",\"code\":\"AUTHENTICATION_REQUIRED\",\"requestId\":\"$request_id\",\"traceId\":\"$request_id\",\"errors\":[]}'; }\n"
        "        location @platform_forbidden { auth_request off; default_type application/problem+json; return 403 '{\"type\":\"urn:"
        + product_id
        + ":problem:ROLE_FORBIDDEN\",\"title\":\"Role forbidden\",\"status\":403,\"detail\":\"The verified identity is not allowed to access this surface.\",\"code\":\"ROLE_FORBIDDEN\",\"requestId\":\"$request_id\",\"traceId\":\"$request_id\",\"errors\":[]}'; }\n"
        + "\n".join(locations)
        + "\n    }\n}\n"
    )
