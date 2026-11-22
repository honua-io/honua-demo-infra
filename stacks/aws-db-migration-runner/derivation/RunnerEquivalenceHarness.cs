using System.Reflection;
using DbUp;
using DbUp.ScriptProviders;
using Honua.Core.Configuration;
using Honua.Core.Features.Infrastructure.Migrations;
using Honua.Db.Postgres.Features.Infrastructure.Migrations;
using Honua.Db.Postgres.Features.Raster;
using Honua.Server.Startup;
using Microsoft.Extensions.Configuration;
using Npgsql;
using Xunit;

namespace Honua.Server.Tests.ZzHarness;

public sealed class RunnerEquivalenceHarness
{
    [Fact]
    public async Task Run()
    {
        var mode = Environment.GetEnvironmentVariable("HARNESS_MODE");
        var cs = Environment.GetEnvironmentVariable("HARNESS_DB");
        if (string.IsNullOrEmpty(mode) || string.IsNullOrEmpty(cs)) return;
        var serverAssembly = typeof(Honua.Server.Startup.ServerCoreSchemaMigrations).Assembly;
        var output = Environment.GetEnvironmentVariable("HARNESS_OUT")!;
        var approval = Environment.GetEnvironmentVariable("HARNESS_APPROVAL");
        var configuration = new ConfigurationBuilder().AddInMemoryCollection(
            string.IsNullOrEmpty(approval) ? [] : new Dictionary<string, string?> { [MigrationSafetyOptions.ApproveContractMigrationsKey] = approval }).Build();
        var runner = new PostgresDatabaseMigrationRunner(
            new PostgresCoreSchemaGuard(ServerCoreSchemaMigrations.Manifest), ServerCoreSchemaMigrations.Manifest, configuration: configuration);
        if (mode == "baseline")
        {
            var baseline = File.ReadAllLines(Environment.GetEnvironmentVariable("HARNESS_BASELINE")!).Where(l => l.Length > 0).ToHashSet();
            var migrationCs = new NpgsqlConnectionStringBuilder(cs) { SearchPath = "public" }.ConnectionString;
            var builder = DeployChanges.To.PostgresqlDatabase(migrationCs)
                .JournalToPostgresqlTable("public", "schema_versions")
                .WithVariable("HonuaSchema", "\"honua\"")
                .WithTransaction()
                .LogToConsole();
            foreach (var (name, body) in RasterSamplingSql.MigrationVariables()) builder = builder.WithVariable(name, body);
            foreach (var assembly in new[] { typeof(PostgresDatabaseMigrationRunner).Assembly, serverAssembly })
            {
                builder.WithScripts(new EmbeddedScriptProvider(assembly, n =>
                    n.EndsWith(".sql", StringComparison.OrdinalIgnoreCase) && baseline.Contains(n)));
            }
            var cmp = typeof(PostgresDatabaseMigrationRunner).GetField("_migrationScriptNameComparer", BindingFlags.NonPublic | BindingFlags.Static)!.GetValue(null)!;
            var upgrader = builder.WithScriptNameComparer((IComparer<string>)cmp).Build();
            var result = upgrader.PerformUpgrade();
            if (!result.Successful) throw result.Error;
            await using var conn = new NpgsqlConnection(migrationCs);
            await conn.OpenAsync();
            await PostgresDatabaseMigrationRunner.RecordReaderCompatibilityDeclarationsAsync(conn, upgrader, CancellationToken.None);
            File.WriteAllText(output, "baseline applied " + result.Scripts.Count() + "\n");
        }
        else
        {
            var plan = await runner.PlanMigrationsAsync(cs, serverAssembly);
            var text = $"plan ok={plan.Successful} pending={plan.PendingScripts?.Count ?? -1} nonce={plan.ContractApprovalNonce} undiscovered=[{string.Join(",", plan.ExecutedButNotDiscoveredScripts ?? [])}] err={plan.ErrorMessage}\n";
            if (mode == "verify")
            {
                // The same schema-floor guard /healthz/ready consults with HONUA_SKIP_MIGRATIONS=true.
                try
                {
                    await new PostgresCoreSchemaGuard(ServerCoreSchemaMigrations.Manifest).VerifyAsync(cs);
                    text += "guard ok\n";
                }
                catch (Exception ex)
                {
                    text += $"guard FAILED {ex.GetType().Name}: {ex.Message}\n";
                }
            }
            if (mode == "reference")
            {
                var run = await runner.RunMigrationsAsync(cs, serverAssembly);
                text += $"run ok={run.Successful} applied={run.AppliedScripts?.Count ?? 0} err={run.ErrorMessage}\n";
            }
            File.WriteAllText(output, text);
        }
    }
}
