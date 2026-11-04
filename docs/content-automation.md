# Content automation

Two GitHub Actions keep the site ticking over. They are free. They do not publish anything by themselves. A blog post goes live only when you merge its pull request.

The workflows run from the main branch:

- **Site health check**, Monday at 06:00 UTC (6:00am UK time in winter, 7:00am in British Summer Time). It also has a button to run it by hand.
- **Draft blog post**, Tuesday and Thursday at 07:00 UTC (7:00am UK time in winter, 8:00am in British Summer Time). It can also be run by hand.

GitHub sometimes starts a scheduled run a few minutes late. Scheduled runs also stop if the repository has no commits for about 60 days. Editing the calendar counts as a commit.

## Add a topic

Open `content/calendar.csv`. The first line stays as it is. Add one topic per line, under it:

```
date,topic,target_query,status,notes
2026-12-01,Your topic here,your target search,queued,Optional note
```

- **date** is the day you want on the post, written as `2026-12-01`.
- **topic** is the working title. The draft uses it as the heading.
- **target_query** is the search you want the post to be useful for. It can be left blank.
- **status** is `queued` when you want a draft. The workflow ignores every other status. Use `skip` to park a row.
- **notes** is optional. A comma inside a field needs quotes, as in `"first month, in practice"`.

The next run takes the earliest `queued` date. It does not wait for that date to arrive. Add a row when you are happy for it to be drafted. On the phone, the simplest edit is github.com in the browser: open the file, use the pencil, commit the change to main.

## Review and merge on your phone

1. Open the GitHub app and sign in.
2. Open **Pull requests**.
3. Open the one titled **Draft: …**.
4. Open **Files** and read the post. Look for `[FAISAL TO ADD]`. That marker is standing in for a figure, client, quote or result. Do not leave it in place unless you mean to.
5. To change the words, edit the file on that branch (the browser pencil is the reliable way), commit to the branch, then come back to the pull request.
6. Tap **Merge** when you are happy. The page, the Insights list and the sitemap update together. The site follows a minute or two later.
7. Tap **Close** if you do not want it. Main still says `queued`, so a later run will draft it again. Set the row to `skip` on main if you want it dropped.

These are normal pull requests. The word Draft is only in the title, so the Merge button is there. You do not have to press "Ready for review".

The new card is added at the top of the Insights list, not in the Outside In teardowns. The breadcrumb is Home, then Insights, then the title, same as the other posts.

Only one draft is opened at a time. If a draft pull request is still open, the Tuesday or Thursday run waits. Merge it or close it, and the next run picks up the calendar.

When you merge, that same pull request marks the calendar row as `drafted` and adds the pull request link. You do not need to edit the status yourself.

## Run a workflow by hand

1. Open the repository on github.com (the phone browser is fine).
2. Open **Actions**.
3. Choose **Site health check** or **Draft blog post**.
4. Open **Run workflow**, leave the branch as main, and run it.

A health run opens one issue labelled `site-health` and closes the previous one, so they do not pile up. A draft run opens a pull request, or does nothing if a draft is already open or no row is `queued`. A red run means the action itself failed. Open the run and read the log.

## Add the Gemini key

You do this yourself. The key stays in GitHub's secret store. It is not written in the repository, in an issue, or in a chat.

1. Create a free key in Google AI Studio: [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
2. In the repository, open **Settings → Secrets and variables → Actions**.
3. Choose **New repository secret**.
4. Name it exactly `GEMINI_API_KEY`.
5. Paste the key and save.

The next draft then asks Gemini to write the body. The instructions to the model are: UK English, short sentences, no hype, and no invented figures, clients, quotes or results. Anything that needs a real number is written as `[FAISAL TO ADD]`.

If the secret is not set, or Gemini does not answer, you still get a pull request. The page has the usual layout and headings from the topic, and `[FAISAL TO ADD]` where the paragraphs go.

## If a run cannot open a pull request

The draft workflow is allowed to push a branch and open a pull request. It is not allowed to push to main. If a run fails on the push, check **Settings → Actions → General → Workflow permissions** and choose **Read and write permissions**. If there is a box for allowing GitHub Actions to create pull requests, turn that on too.

## What these workflows will not do

They will not merge their own pull requests, edit a live page in place, or commit straight to main. They will not invent a topic. An empty calendar produces no draft.
