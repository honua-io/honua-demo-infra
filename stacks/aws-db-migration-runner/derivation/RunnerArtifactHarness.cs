using System.Reflection;
using System.Text;
using System.Text.Json;
using DbUp.Builder;
using DbUp.Engine;
using Honua.Core.Features.Infrastructure.Migrations;
using Honua.Db.Postgres.Features.Infrastructure.Migrations;
using Honua.Db.Postgres.Features.Raster;
using Xunit;

namespace Honua.Server.Tests.ZzHarness;

public sealed class RunnerArtifactHarness
{
    [Fact]
    public void Emit()
    {
        var output = Environment.GetEnvironmentVariable("HARNESS_OUT");
        if (string.IsNullOrEmpty(output)) return;
        var pending = Environment.GetEnvironmentVariable("HARNESS_PENDING")!.Split(',');
        var serverAssembly = typeof(Honua.Server.EndpointRegistry).Assembly;
        var upgrader = PostgresDatabaseMigrationRunner.BuildUpgrader(
            "Host=127.0.0.1;Database=x;Username=x;Password=x",
            serverAssembly,
            Honua.Db.Postgres.Features.Infrastructure.SchemaSearchPath.ValidateAndQuote("honua"),
            includeRasterProviderMigrations: true,
            includeConfiguredSchemaAdoption: false,
            configuredSchemaAdoptionMigration: "Honua.Server.Migrations.109_AdoptConfiguredGuardedSchema.sql");
        var discovered = upgrader.GetDiscoveredScripts();
        var config = (UpgradeConfiguration)typeof(UpgradeEngine).GetFields(BindingFlags.NonPublic | BindingFlags.Instance).Single(f => f.FieldType == typeof(UpgradeConfiguration)).GetValue(upgrader)!;
        var executor = config.ScriptExecutor;
        var preprocess = executor.GetType().GetMethod("PreprocessScriptContents", BindingFlags.NonPublic | BindingFlags.Instance)!;
        var scripts = new List<object>();
        var classifications = new List<MigrationScriptClassification>();
        foreach (var name in pending)
        {
            var script = discovered.Single(s => s.Name == name);
            var rendered = (string)preprocess.Invoke(executor, [script, new Dictionary<string, string>(config.Variables)])!;
            var commands = config.ConnectionManager.SplitScriptIntoCommands(rendered).ToArray();
            var declaration = MigrationReaderCompatibility.Declare(script.Name, script.Contents);
            var classification = MigrationSafetyClassifier.Classify(script.Name, script.Contents);
            classifications.Add(classification);
            scripts.Add(new
            {
                name = script.Name,
                classification = classification.Classification.ToString(),
                breakingRules = classification.BreakingRules,
                rawSha256 = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(Encoding.UTF8.GetBytes(script.Contents))).ToLowerInvariant(),
                commands,
                declaration,
            });
        }
        var order = discovered.Select(s => s.Name).Where(pending.Contains).ToArray();
        var known = discovered.Select(s => s.Name).ToArray();
        var nonce = PostgresDatabaseMigrationRunner.ComputeApprovalNonce(classifications, []);
        File.WriteAllText(output, JsonSerializer.Serialize(new { order, known, nonce, scripts }, new JsonSerializerOptions { WriteIndented = true }));
    }
}
