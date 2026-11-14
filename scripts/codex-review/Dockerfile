# Build with --network=none --platform=linux/arm64 --pull=false. The build
# context contains only this file and the independently verified Linux ARM64 runtime.
FROM node@sha256:0557ac14e0d45d02ed563067b82856ca5e7aa3437fa28d98d4350ea9c3d9494a AS runtime
COPY --chown=0:0 runtime/ /runtime/
ARG CODEX_BINARY_SHA256
RUN ["/usr/bin/python3", "-I", "-c", "import hashlib,os,pathlib,re; expected=os.environ.get('CODEX_BINARY_SHA256',''); p=pathlib.Path('/runtime/bin/codex'); assert re.fullmatch('[0-9a-f]{64}',expected) and hashlib.sha256(p.read_bytes()).hexdigest() == expected; [(os.chmod(f,0o755 if f.is_dir() or f.stat().st_mode & 0o111 else 0o644)) for f in pathlib.Path('/runtime').rglob('*')]; os.chmod('/runtime',0o755)"]
# Preserve the selected filesystem without inheriting image ENV or entrypoints.
FROM scratch
COPY --from=runtime / /
ENV PATH=/usr/bin:/bin
USER 65534:65534
WORKDIR /review
ENTRYPOINT ["/runtime/bin/codex"]
