ARG SOURCE_DATE_EPOCH=0
FROM docker.io/hexpm/elixir@sha256:df1f6fe1ffb340380b5089bdf4eed8fcef2305e5909e272b4587fb7864b7ab10 AS otp
FROM docker.io/library/node@sha256:4e9cb555d708e0829c9d93e5eeae9dfab0617b832ca436a690680e0fca735ef5
COPY --from=otp /usr/local/ /usr/local/
USER 1001:1001
ENV HOME=/home/ci TMPDIR=/tmp MIX_HOME=/cache/mix HEX_HOME=/cache/hex LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC
LABEL com.symphony.ci.profile="elixir-coverage-oci-preflight-v1"
