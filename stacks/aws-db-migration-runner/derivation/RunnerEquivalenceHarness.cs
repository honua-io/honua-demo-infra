using System.Reflection;
using DbUp;
using DbUp.Engine;
using DbUp.ScriptProviders;
using Honua.Db.Postgres.Features.Infrastructure.Migrations;
using Honua.Db.Postgres.Features.Raster;
using Honua.Server.Startup;
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
        var serverAssembly = typeof(Honua.Server.EndpointRegistry).Assembly;
        var output = Environment.GetEnvironmentVariable("HARNESS_OUT")!;
        var runner = new PostgresDatabaseMigrationRunner(
            new PostgresCoreSchemaGuard(ServerCoreSchemaMigrations.Manifest), ServerCoreSchemaMigrations.Manifest);
        if (mode == "baseline")
        {
            var pending = Environment.GetEnvironmentVariable("HARNESS_PENDING")!.Split(',').ToHashSet();
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
                    n.EndsWith(".sql", StringComparison.OrdinalIgnoreCase)
                    && n != "Honua.Server.Migrations.109_AdoptConfiguredGuardedSchema.sql"
                    && !pending.Contains(n)));
            }
            var cmp = typeof(PostgresDatabaseMigrationRunner).GetField("_migrationScriptNameComparer", BindingFlags.NonPublic | BindingFlags.Static)!.GetValue(null)!;
            var upgrader = builder.WithScriptNameComparer((IComparer<string>)cmp).Build();
            var result = upgrader.PerformUpgrade();
            if (!result.Successful) throw result.Error;
            await using var conn = new NpgsqlConnection(migrationCs);
            await conn.OpenAsync();
            await PostgresDatabaseMigrationRunner.RecordReaderCompatibilityDeclarationsAsync(conn, upgrader, CancellationToken.None);
            File.WriteAllText(output, "baseline applied " + result.Scripts.Count());
        }
        else if (mode == "reference" || mode == "verify")
        {
            var plan = await runner.PlanMigrationsAsync(cs, serverAssembly);
            var run = await runner.RunMigrationsAsync(cs, serverAssembly);
            File.WriteAllText(output, $"plan ok={plan.Successful} pending=[{string.Join(",", plan.PendingScripts ?? [])}] undiscovered=[{string.Join(",", plan.ExecutedButNotDiscoveredScripts ?? [])}] err={plan.ErrorMessage}\nrun ok={run.Successful} applied={run.AppliedScripts?.Count ?? 0} err={run.ErrorMessage}\n");
        }
    }
}
