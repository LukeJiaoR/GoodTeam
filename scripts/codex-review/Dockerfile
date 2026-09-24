# Build with --network=none --platform=linux/arm64 --pull=false. The build
# context contains only this file and the verified 0.155.1 Linux ARM64 runtime.
FROM node@sha256:0557ac14e0d45d02ed563067b82856ca5e7aa3437fa28d98d4350ea9c3d9494a AS runtime
COPY --chown=0:0 runtime/ /runtime/
RUN ["/usr/bin/python3", "-I", "-c", "import hashlib,os,pathlib; p=pathlib.Path('/runtime/bin/codex'); assert hashlib.sha256(p.read_bytes()).hexdigest() == '298d3d73d0bbc1367e58a370df5b6216fe30ce0a92e8b6b0afb0377a958dc335'; [(os.chmod(f,0o755 if f.is_dir() or f.stat().st_mode & 0o111 else 0o644)) for f in pathlib.Path('/runtime').rglob('*')]; os.chmod('/runtime',0o755)"]
# Preserve the selected filesystem without inheriting image ENV or entrypoints.
FROM scratch
COPY --from=runtime / /
ENV PATH=/usr/bin:/bin
USER 65534:65534
WORKDIR /review
ENTRYPOINT ["/runtime/bin/codex"]
