$headers = @{
    "x-goog-api-key" = ""

}

try {
    $r = Invoke-RestMethod `
        -Method GET `
        -Uri "https://generativelanguage.googleapis.com/v1/interactions/v1_ChdUcnJBYXFHaE5jem03TThQaGJeWlBdxIXVHJyQWFxR2hOY3ptN004UGhiTHlpQXc" `
        -Headers $headers

    $r | ConvertTo-Json -Depth 20
}
catch {
    Write-Host "HTTP error:"
    Write-Host $_.Exception.Message

    if ($_.Exception.Response) {
        $reader = New-Object System.IO.StreamReader(
            $_.Exception.Response.GetResponseStream()
        )

        Write-Host "`nAPI response body:"
        Write-Host $reader.ReadToEnd()
    }
}