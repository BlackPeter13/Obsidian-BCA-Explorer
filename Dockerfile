# NBXplorer 2.0.0.7 + BCA (BitcoinAtom) support, ported to .NET 8.
# Multi-stage: SDK builds, ASP.NET runtime serves. NuGet restore needs
# network (nuget.org) and the MCR base images; the running container
# only talks to the BCA daemon on the LAN.
FROM mcr.microsoft.com/dotnet/sdk:8.0-bookworm-slim AS builder

WORKDIR /src
# Dependency layer first: csproj + NuGet config only, keeps restore cached
# across source edits. Restore uses vendored feed with nuget.org fallback.
COPY NuGet.config ./.
COPY NBXplorer/NBXplorer.csproj NBXplorer/
COPY NBXplorer.Client/NBXplorer.Client.csproj NBXplorer.Client/
# Vendored feed is optional — NuGet.config falls back to nuget.org.
RUN mkdir -p ./vendor/nuget && dotnet restore NBXplorer/NBXplorer.csproj --nologo

COPY . .
# No --no-restore: publish re-validates the graph itself (offline feed).
RUN dotnet publish NBXplorer/NBXplorer.csproj \
    -c Release -o /app --nologo


FROM mcr.microsoft.com/dotnet/aspnet:8.0-bookworm-slim

ENV DOTNET_RUNNING_IN_CONTAINER=true \
    NBXPLORER_DATADIR=/datadir

# curl only for the HEALTHCHECK, from apt (vendored .debs optional).
RUN apt-get update -qq && apt-get install -y -qq curl \
    && useradd -m -d /datadir -s /usr/sbin/nologin nbxplorer \
    && mkdir -p /datadir

COPY --from=builder /app /app
RUN chown -R nbxplorer:nbxplorer /app /datadir

VOLUME ["/datadir"]
EXPOSE 24444
USER nbxplorer
WORKDIR /datadir
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:24444/v1/cryptos/bca/status > /dev/null || exit 1
ENTRYPOINT ["dotnet", "/app/NBXplorer.dll"]
