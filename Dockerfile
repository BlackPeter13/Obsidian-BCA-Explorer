# NBXplorer 2.0.0.7 + BCA (BitcoinAtom) support, ported to .NET 8.
# Multi-stage: SDK builds, ASP.NET runtime serves. NuGet restore needs
# network (nuget.org) and the MCR base images; the running container
# only talks to the BCA daemon on the LAN.
FROM mcr.microsoft.com/dotnet/sdk:8.0-bookworm-slim AS builder

WORKDIR /src
# Dependency layer first: csproj + NuGet config only, keeps restore cached
# across source edits. Restore is fully offline (vendored feed, no remotes).
COPY NuGet.config ./
COPY NBXplorer/NBXplorer.csproj NBXplorer/
COPY NBXplorer.Client/NBXplorer.Client.csproj NBXplorer.Client/
# Vendored feed must arrive BEFORE restore (offline by construction).
COPY vendor/nuget/ ./vendor/nuget/
RUN dotnet restore NBXplorer/NBXplorer.csproj --nologo

COPY . .
# No --no-restore: publish re-validates the graph itself (offline feed).
RUN dotnet publish NBXplorer/NBXplorer.csproj \
    -c Release -o /app --nologo


FROM mcr.microsoft.com/dotnet/aspnet:8.0-bookworm-slim

ENV DOTNET_RUNNING_IN_CONTAINER=true \
    NBXPLORER_DATADIR=/datadir

# curl only for the HEALTHCHECK, from vendored .debs (no apt network).
# dpkg (not apt): few packages, deps already in the base image.
COPY vendor/debs-runtime/*.deb /tmp/debs-runtime/
# dpkg (not apt): two-phase so install order never matters, no lists needed.
RUN dpkg --unpack /tmp/debs-runtime/*.deb && \
    dpkg --configure -a && \
    rm -rf /tmp/debs-runtime && \
    useradd -m -d /datadir -s /usr/sbin/nologin nbxplorer && \
    mkdir -p /datadir

COPY --from=builder /app /app
RUN chown -R nbxplorer:nbxplorer /app /datadir

VOLUME ["/datadir"]
EXPOSE 24444
USER nbxplorer
WORKDIR /datadir
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:24444/v1/cryptos/bca/status > /dev/null || exit 1
ENTRYPOINT ["dotnet", "/app/NBXplorer.dll"]
