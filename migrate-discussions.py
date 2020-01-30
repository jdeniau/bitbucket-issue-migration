#!/usr/bin/env python3
import re
from dateutil import parser
import argparse
from github import InputFileContent
import config
from migrate.bitbucket import BitbucketExport
from migrate.github import GithubImport
from commit_map.map import CommitMap


EXPLICIT_ISSUE_LINK_RE = re.compile(r'https://bitbucket.org/({repos})/issues*/(\d+)[^\s()\[\]{{}}]*'
                           .format(repos="|".join(config.KNOWN_REPO_MAPPING)))
def replace_explicit_links_to_issues(body):
    # replace explicit links to other issues by an explicit link to GitHub (instead of "#<id>").
    # This avoids that "#<id>" in a Markdown link will be interpreted as a relative link
    def replace_issue_link(match):
        brepo = match.group(1)
        issue_nr = match.group(2)
        if brepo not in config.KNOWN_REPO_MAPPING:
            # leave link unchanged:
            return match.group(0)
        grepo = config.KNOWN_REPO_MAPPING[brepo]
        return r'https://github.com/{repo}/issues/{issue_nr}'.format(
            repo=grepo, issue_nr=issue_nr)
    return EXPLICIT_ISSUE_LINK_RE.sub(replace_issue_link, body)


# test for all known repo names (separated by a single whitespace from issue)
# the disjunction ensures that text between squared brackets is not captured
IMPLICIT_ISSUE_LINK_RE = re.compile(r'\[.*?\]|({repo_names})?issue #(\d+)/i'
                           .format(repo_names="|".join([repo.split('/')[-1] + " "
                                                        for repo in config.KNOWN_REPO_MAPPING])))
def replace_implicit_links_to_issues(body, args):
    def replace_issue_link(match):
        repo_name = match.group(1)
        issue_nr = match.group(2)
        if issue_nr is None:
            # first disjuncted term was matched, i.e. squared brackets
            # leave unchanged:
            return match.group(0)
        grepo = None
        for brepo in config.KNOWN_REPO_MAPPING:
            if repo_name == brepo.split('/')[-1] + " ":
                grepo = config.KNOWN_REPO_MAPPING[brepo]
                break
        if grepo is None:
            # interpret as same repo link
            grepo = args.github_repository
        return r'https://github.com/{repo}/issues/{issue_nr}'.format(
            repo=grepo, issue_nr=issue_nr)
    return IMPLICIT_ISSUE_LINK_RE.sub(replace_issue_link, body)


EXPLICIT_PR_LINK_RE = re.compile(r'https://bitbucket.org/({repos})/pull-requests*/(\d+)[^\s()\[\]{{}}]*'
                           .format(repos="|".join(config.KNOWN_REPO_MAPPING)))
def replace_explicit_links_to_prs(body):
    # Bitbucket uses separate numbering for issues and pull requests
    # However, GitHub uses the same numbering.
    # Assuming that pull requests get imported after issues, the IDs of pull requests need to be incremented by the
    # number of issues (in the corresponding repo)
    def replace_pr_link(match):
        brepo = match.group(1)
        bpr_nr = int(match.group(2))
        if brepo not in config.KNOWN_REPO_MAPPING or brepo not in config.KNOWN_ISSUES_COUNT_MAPPING:
            # leave link unchanged:
            return match.group(0)
        grepo = config.KNOWN_REPO_MAPPING[brepo]
        issues_count = config.KNOWN_ISSUES_COUNT_MAPPING[brepo]
        gpr_number = bpr_nr + issues_count
        return r'https://github.com/{repo}/pull/{gpr_number}'.format(
            repo=grepo, gpr_number=gpr_number)
    return EXPLICIT_PR_LINK_RE.sub(replace_pr_link, body)


# test for all known repo names (separated by a single whitespace from issue)
# the disjunction ensures that text between squared brackets is not captured
IMPLICIT_PR_LINK_RE = re.compile(r'\[.*?\]|({repo_names})?pull request #(\d+)/i'
                           .format(repo_names="|".join([repo.split('/')[-1] + " "
                                                        for repo in config.KNOWN_REPO_MAPPING])))
def replace_implicit_links_to_prs(body, args):
    def replace_issue_link(match):
        repo_name = match.group(1)
        bpr_nr = match.group(2)
        if bpr_nr is None:
            # first disjuncted term was matched, i.e. squared brackets
            # leave unchanged:
            return match.group(0)
        brepo = None
        grepo = None
        for repo in config.KNOWN_REPO_MAPPING:
            if repo_name == repo.split('/')[-1] + " ":
                brepo = repo
                grepo = config.KNOWN_REPO_MAPPING[repo]
                break
        if brepo is None or grepo is None:
            # interpret as same repo link
            brepo = args.bitbucket_repository
            grepo = args.github_repository
        if brepo not in config.KNOWN_ISSUES_COUNT_MAPPING:
            # leave unchanged:
            return match.group(0)
        issues_count = config.KNOWN_ISSUES_COUNT_MAPPING[brepo]
        gpr_number = bpr_nr + issues_count
        return r'https://github.com/{repo}/pull/{gpr_number}'.format(
            repo=grepo, gpr_number=gpr_number)
    return IMPLICIT_PR_LINK_RE.sub(replace_issue_link, body)


MENTION_RE = re.compile(r'(?:^|(?<=[^\w]))@([a-zA-Z0-9_\-]+|{[a-zA-Z0-9_\-:]+})')
def replace_links_to_users(body):
    # replace @mentions with users specified in config:
    # TODO: remove the 'ignore_' before doing the real migration
    def replace_user(match):
        buser = match.group(1)
        guser = lookup_user(buser)
        if guser is None:
            # leave username unchanged:
            return '@' + 'ignore_' + buser
        return '@' + guser
    return MENTION_RE.sub(replace_user, body)


def map_bstate_to_gstate(bissue):
    bstate = bissue["state"]
    if bstate in config.OPEN_ISSUE_OR_PULL_REQUEST_STATES:
        return "open"
    else:
        return "closed"


def lookup_user(buser_nickname):
    if buser_nickname not in config.USER_MAPPING:
        return 'ignore_' + buser_nickname
    return 'ignore_' + config.USER_MAPPING[buser_nickname]


def map_buser_to_guser(buser):
    if buser is None:
        return None
    else:
        nickname = buser["nickname"]
        return lookup_user(nickname)


def map_brepo_to_grepo(brepo):
    if brepo not in config.KNOWN_REPO_MAPPING:
        return None
    return config.KNOWN_REPO_MAPPING[brepo]


def map_bstate_to_glabels(bissue):
    bstate = bissue["state"]
    if bstate in config.STATE_MAPPING:
        label = config.STATE_MAPPING[bstate]
        if label is None:
            return []
        else:
            return [label]
    else:
        print("Warning: ignoring bitbucket issue state '{}'".format(bstate))
        return []


def map_bpriority_to_glabels(bissue):
    bpriority = bissue["priority"]
    if bpriority in config.PRIORITY_MAPPING:
        label = config.PRIORITY_MAPPING[bpriority]
        if label is None:
            return []
        else:
            return [label]
    else:
        print("Warning: ignoring bitbucket issue priority '{}'".format(bpriority))
        return []


def map_bkind_to_glabels(bissue):
    bkind = bissue["kind"]
    if bkind in config.KIND_MAPPING:
        label = config.KIND_MAPPING[bkind]
        if label is None:
            return []
        else:
            return [label]
    else:
        print("Warning: ignoring bitbucket issue kind '{}'".format(bkind))
        return []


def map_bcomponent_to_glabels(bissue):
    if bissue["component"] is None:
        return []
    bcomponent = bissue["component"]["name"]
    if bcomponent in config.COMPONENT_MAPPING:
        label = config.COMPONENT_MAPPING[bcomponent]
        if label is None:
            return []
        else:
            return [label]
    else:
        print("Warning: ignoring bitbucket issue component '{}'".format(bcomponent))
        return []


# maps the raw content of issues, pull requests, and comments to new content for GitHub by replacing links
# and user mentions
def map_content(content, args):
    tmp = replace_explicit_links_to_issues(content)
    tmp = replace_implicit_links_to_issues(tmp, args)
    tmp = replace_explicit_links_to_prs(tmp)
    tmp = replace_implicit_links_to_prs(tmp, args)
    return replace_links_to_users(tmp)


def time_string_to_date_string(timestring):
    datetime = parser.parse(timestring)
    return datetime.strftime("%Y-%m-%d %H:%M")


def convert_date(bb_date):
    """Convert the date from Bitbucket format to GitHub format."""
    # '2012-11-26T09:59:39+00:00'
    m = re.search(r'(\d\d\d\d-\d\d-\d\d)T(\d\d:\d\d:\d\d)', bb_date)
    if m:
        return '{}T{}Z'.format(m.group(1), m.group(2))

    raise RuntimeError("Could not parse date: {}".format(bb_date))


def construct_gcomment_body(bcomment, bcomments_by_id, args):
    sb = []
    comment_created_on = time_string_to_date_string(bcomment["created_on"])
    sb.append("> **@" + map_buser_to_guser(bcomment["user"]) + "** commented on " + comment_created_on + "\n")
    if "inline" in bcomment:
        inline_data = bcomment["inline"]
        file_path = inline_data["path"]
        if inline_data["from"] is None:
            if inline_data["to"] is None:
                sb.append("> Inline comment on `{}`\n".format(
                    file_path
                ))
            else:
                to_line = inline_data["to"]
                sb.append("> Inline comment on line {} of `{}`\n".format(
                    to_line,
                    file_path
                ))
        else:
            from_line = inline_data["from"]
            to_line = inline_data["to"]
            sb.append("> Inline comment on lines {}..{} of `{}`\n".format(
                from_line,
                to_line,
                file_path
            ))
    sb.append("\n")
    if "parent" in bcomment:
        parent_comment = bcomments_by_id[bcomment["parent"]["id"]]
        if parent_comment["content"]["raw"] is not None:
            sb.append("> {}\n\n".format(map_content(parent_comment["content"]["raw"], args)))
    sb.append("" if bcomment["content"]["raw"] is None else map_content(bcomment["content"]["raw"], args))
    return "".join(sb)


def construct_gissue_body(bissue, battachments, attachment_gist_by_issue_id, args):
    sb = []

    # Header
    created_on = time_string_to_date_string(bissue["created_on"])
    updated_on = time_string_to_date_string(bissue["updated_on"])
    sb.append("> Created by **@" + map_buser_to_guser(bissue["reporter"]) + "** on " + created_on + "\n")
    if created_on != updated_on:
        sb.append("> Last updated on " + updated_on + "\n")

    # Content
    sb.append("\n")
    sb.append(map_content(bissue["content"]["raw"], args))
    sb.append("\n")

    # Attachments
    if battachments:
        sb.append("\n")
        sb.append("---\n")
        sb.append("\n")
        sb.append("Attachments:\n")
        for name in battachments.keys():
            issue_id = bissue["id"]
            if issue_id in attachment_gist_by_issue_id:
                attachments_gist = attachment_gist_by_issue_id[issue_id]
                sb.append("* [**`{}`**]({})\n".format(
                    name,
                    attachments_gist.files[name].raw_url
                ))
            else:
                print("Error: missing gist for the attachments of issue #{}.".format(issue_id))
                sb.append("* **`{}`** (missing link)\n".format(name))

    return "".join(sb)


def construct_gpull_request_body(bpull, bexport, cmap, args):
    sb = []

    # Header
    created_on = time_string_to_date_string(bpull["created_on"])
    updated_on = time_string_to_date_string(bpull["updated_on"])
    if bpull["author"] is None:
        author_msg = ""
    else:
        author_msg = "by **@" + map_buser_to_guser(bpull["author"]) + "** "
    sb.append(">  **Pull request** :twisted_rightwards_arrows: created " + author_msg + "on " + created_on + "\n")
    if created_on != updated_on:
        sb.append("> Last updated on " + updated_on + "\n")

    if bpull["participants"]:
        sb.append(">\n")
        sb.append("> Participants:\n")
        sb.append(">\n")
        for participant in bpull["participants"]:
            sb.append("> * **@{}**".format(map_buser_to_guser(participant["user"])))
            if participant["role"] == "REVIEWER":
                sb.append(" (reviewer)")
            if participant["approved"]:
                sb.append(" :heavy_check_mark:")
            sb.append("\n")

    sb.append(">\n")
    source = bpull["source"]
    if source["repository"] is None and source["commit"] is None:
        source_brepo = bexport.get_repo_full_name()
        source_bbranch = source["branch"]["name"]
        source_grepo = map_brepo_to_grepo(source_brepo)
        source_gbranch = cmap.convert_branch_name(source_bbranch)
        sb.append("> Source: branch [`{branch}`](https://github.com/{grepo}/tree/{gbranch})\n".format(
            branch=source_gbranch,
            grepo=source_grepo,
            gbranch=source_gbranch
        ))
    else:
        source_brepo = source["repository"]["full_name"]
        source_bbranch = source["branch"]["name"]
        source_bhash = source["commit"]["hash"]
        source_grepo = map_brepo_to_grepo(source_brepo)
        source_gbranch = cmap.convert_branch_name(source_bbranch, source_brepo)
        source_ghash = cmap.convert_commit_hash(source_bhash)
        if source_ghash is None:
            print("Error: could not map mercurial commit '{}' (source of a PR) to git.".format(source_bhash))
        sb.append("> Source: branch [`{gbranch}`](https://github.com/{grepo}/tree/{gbranch}), [{ghash}](https://github.com/{grepo}/commit/{ghash})\n".format(
            grepo=source_grepo,
            gbranch=source_gbranch,
            ghash=source_ghash
        ))

    destination = bpull["destination"]
    destination_brepo = destination["repository"]["full_name"]
    destination_bbranch = destination["branch"]["name"]
    destination_bhash = destination["commit"]["hash"]
    destination_grepo = map_brepo_to_grepo(destination_brepo)
    destination_gbranch = cmap.convert_branch_name(destination_bbranch, destination_brepo)
    destination_ghash = cmap.convert_commit_hash(destination_bhash)
    if destination_brepo != bexport.get_repo_full_name():
        print("Error: the destination of a pull request, '{}', is not '{}'.".format(destination_brepo, bexport.get_repo_full_name()))
    if source_ghash is None:
        print("Error: could not map mercurial commit '{}' (destination of a PR) to git.".format(source_bhash))
    sb.append("> Destination: branch [`{gbranch}`](https://github.com/{grepo}/tree/{gbranch}), [{ghash}](https://github.com/{grepo}/commit/{ghash})\n".format(
        grepo=destination_grepo,
        gbranch=destination_gbranch,
        ghash=destination_ghash
    ))

    if bpull["merge_commit"] is not None:
        merge_brepo = bexport.get_repo_full_name()
        merge_bhash = bpull["merge_commit"]["hash"]
        merge_grepo = map_brepo_to_grepo(merge_brepo)
        merge_ghash = cmap.convert_commit_hash(merge_bhash)
        sb.append("> Marge commit: [{ghash}](https://github.com/{grepo}/commit/{ghash})\n".format(
            grepo=merge_grepo,
            ghash=merge_ghash
        ))

    sb.append(">\n")
    sb.append("> State: **`{}`**\n".format(bpull["state"]))

    # Content
    sb.append("\n")
    sb.append(map_content(bpull["description"], args))
    sb.append("\n")

    return "".join(sb)


def construct_gcomment_body_for_change(bchange):
    created_on = time_string_to_date_string(bchange["created_on"])
    sb = []
    blacklist = ["content", "title", "assignee_account_id"]
    for changed_key, change in bchange["changes"].items():
        if changed_key not in blacklist:
            sb.append("> **@{}** changed `{}` from `{}` to `{}` on {}\n".format(
                map_buser_to_guser(bchange["user"]),
                changed_key,
                change["old"] if change["old"] else "(none)",
                change["new"] if change["new"] else "(none)",
                created_on
            ))
    return "".join(sb)


def construct_gcomment_body_for_update_activity(update_activity):
    on_date = time_string_to_date_string(update_activity["date"])
    if update_activity["author"] is None:
        return "> the status has been changed to `{}` on {}".format(
            update_activity["state"],
            on_date
        )
    else:
        return "> **@{}** changed the status to `{}` on {}".format(
            map_buser_to_guser(update_activity["author"]),
            update_activity["state"],
            on_date
        )


def construct_gcomment_body_for_approval_activity(approval_activity):
    on_date = time_string_to_date_string(approval_activity["date"])
    return "> **@{}** approved :heavy_check_mark: the pull request on {}".format(
        map_buser_to_guser(approval_activity["user"]),
        on_date
    )


def construct_gissue_title_for_pull(bpull):
    return "[PR] " + bpull["title"]


def construct_gissue_comments(bcomments, args):
    comments = []

    for comment_id, bcomment in bcomments.items():
        # Skip empty comments
        if bcomment["content"]["raw"] is None:
            continue
        # Skip deleted comments
        if "deleted" in bcomment and bcomment["deleted"]:
            continue
        # Constrct comment
        comment = {
            "body": construct_gcomment_body(bcomment, bcomments, args),
            "created_at": convert_date(bcomment["created_on"])
        }
        comments.append(comment)

    comments.sort(key=lambda x: x["created_at"])
    return comments


def construct_gist_description_for_issue_attachments(bissue, bexport):
    return "Attachments for issue #{} of bitbucket repo {}".format(
        bissue["id"],
        bexport.get_repo_full_name()
    )


def construct_gist_from_bissue_attachments(bissue, bexport):
    issue_id = bissue["id"]
    battachments = bexport.get_issue_attachments(issue_id)

    if not battachments:
        return None

    gist_description = construct_gist_description_for_issue_attachments(bissue, bexport)
    gist_files = {"# README.md": InputFileContent(gist_description)}

    for name in battachments.keys():
        content = bexport.get_issue_attachment_content(issue_id, name)
        gist_files[name] = InputFileContent(content)

    return {
        "description": gist_description,
        "files": gist_files
    }


def construct_gissue_comments_for_changes(bchanges):
    comments = []
    for bchange in bchanges:
        body = construct_gcomment_body_for_change(bchange)
        # Skip empty comments
        if body:
            comment = {
                "body": body,
                "created_at": convert_date(bchange["created_on"])
            }
            comments.append(comment)
    return comments


def construct_gissue_comments_for_activity(bactivity):
    comments = []
    for single_activity in bactivity:
        if "approval" in single_activity:
            approval_activity = single_activity["approval"]
            activity_date = approval_activity["date"]
            body = construct_gcomment_body_for_approval_activity(approval_activity)
        else:
            # comment activity or update
            continue
        comment = {
            "body": body,
            "created_at": convert_date(activity_date)
        }
        comments.append(comment)
    return comments


def construct_gissue_from_bissue(bissue, bexport, attachment_gist_by_issue_id, args):
    issue_id = bissue["id"]
    battachments = bexport.get_issue_attachments(issue_id)
    bcomments = bexport.get_issue_comments(issue_id)
    bchanges = bexport.get_issue_changes(issue_id)

    issue_body = construct_gissue_body(bissue, battachments, attachment_gist_by_issue_id, args)

    # Construct comments
    comments = []
    comments += construct_gissue_comments(bcomments, args)
    comments += construct_gissue_comments_for_changes(bchanges)
    comments.sort(key=lambda x: x["created_at"])

    # Construct labels
    labels = (
        map_bkind_to_glabels(bissue) +
        map_bstate_to_glabels(bissue) +
        map_bpriority_to_glabels(bissue) +
        map_bcomponent_to_glabels(bissue)
    )

    return {
        "issue": {
            "title": bissue["title"],
            "body": issue_body,
            "created_at": convert_date(bissue["created_on"]),
            "updated_at": convert_date(bissue["updated_on"]),
            "assignee": map_buser_to_guser(bissue["assignee"]),
            "closed": map_bstate_to_gstate(bissue) == "closed",
            "labels": list(set(labels)),
        },
        "comments": comments
    }


def construct_gissue_or_gpull_from_bpull(bpull, bexport, cmap, args):
    pull_id = bpull["id"]
    bcomments = bexport.get_pull_comments(pull_id)
    bactivity = bexport.get_pull_activity(pull_id)

    issue_body = construct_gpull_request_body(bpull, bexport, cmap, args)

    # Construct comments
    comments = []
    comments += construct_gissue_comments(bcomments, args)
    comments += construct_gissue_comments_for_activity(bactivity)
    comments.sort(key=lambda x: x["created_at"])

    # Construct labels
    labels = ["pull request"] + map_bstate_to_glabels(bpull)

    is_closed = map_bstate_to_gstate(bpull) == "closed"

    if is_closed:
        issue_data = {
            "issue": {
                "title": construct_gissue_title_for_pull(bpull),
                "body": issue_body,
                "created_at": convert_date(bpull["created_on"]),
                "updated_at": convert_date(bpull["updated_on"]),
                "assignee": map_buser_to_guser(bpull["author"]),
                "closed": is_closed,
                "labels": list(set(labels)),
            },
            "comments": comments
        }
        return {"type": "issue", "data": issue_data}
    else:
        pull_data = {
            "pull": {
                "title": construct_gissue_title_for_pull(bpull),
                "body": issue_body,
                "assignees": [map_buser_to_guser(bpull["author"])],
                "closed": is_closed,
                "labels": list(set(labels)),
                "base": "TODO",
                "head": "TODO"
            },
            "comments": comments
        }
        return {"type": "pull", "data": pull_data}


def bitbucket_to_github(bexport, gimport, cmap, args):
    brepo_full_name = bexport.get_repo_full_name()
    issues_and_pulls = []
    attachment_gist_by_issue_id = {}

    # Retrieve data
    bissues = bexport.get_issues()
    bpulls = bexport.get_pulls()
    assert brepo_full_name in config.KNOWN_ISSUES_COUNT_MAPPING
    assert config.KNOWN_ISSUES_COUNT_MAPPING[brepo_full_name] == len(bissues)
    pulls_id_offset = config.KNOWN_ISSUES_COUNT_MAPPING[brepo_full_name]

    # Migrate attachments
    if not args.skip_attachments:
        print("Migrate bitbucket attachments to github...")
        for bissue in bissues:
            issue_id = bissue["id"]
            print("Migrate attachments for bitbucket issue #{}... [rate limiting: {}]".format(issue_id, gimport.get_remaining_rate_limit()))
            battachments = bexport.get_issue_attachments(issue_id)
            if battachments:
                gist_data = construct_gist_from_bissue_attachments(bissue, bexport)
                gist = gimport.get_or_create_gist_by_description(gist_data)
                attachment_gist_by_issue_id[issue_id] = gist
    else:
        print("Warning: migration of bitbucket attachments to github has been skipped.")

    # Prepare issues
    print("Prepare github issues...")
    for bissue in bissues:
        issue_id = bissue["id"]
        print("Prepare github issue #{} from bitbucket issue...".format(issue_id))
        gissue = construct_gissue_from_bissue(bissue, bexport, attachment_gist_by_issue_id, args)
        issues_and_pulls.append({"type": "issue", "data": gissue})

    for bpull in bpulls:
        issue_id = bpull["id"] + pulls_id_offset
        print("Prepare github issue #{} from bitbucket pull request...".format(issue_id))
        gissue_or_gpull = construct_gissue_or_gpull_from_bpull(bpull, bexport, cmap, args)
        issues_and_pulls.append(gissue_or_gpull)

    # Upload github issues
    print("Upload github issues...")
    existing_gissues = gimport.get_issues()
    existing_gpulls = gimport.get_pulls()

    for index, issue_or_pull in enumerate(issues_and_pulls):
        number = index + 1
        type = issue_or_pull["type"]
        data = issue_or_pull["data"]

        print("Upload github issue or pull request #{}... [rate limiting: {}]".format(number, gimport.get_remaining_rate_limit()))

        if type == "issue":
            if number in existing_gissues:
                print("Update github issue #{}...".format(number))
                gimport.update_issue_with_comments(existing_gissues[number], data)
            else:
                print("Create github issue #{}...".format(number))
                gimport.create_issue_with_comments(data)
        elif type == "pull":
            if number in existing_gpulls:
                print("Update github pull request #{}...".format(number))
                gimport.update_issue_with_comments(existing_gpulls[number], data)
            else:
                print("Create github pull request #{}...".format(number))
                gimport.create_pull_with_comments(data)
        else:
            print("Error: unknown type '{}' for data '{}'".format(type, data))

    # Final checks
    if len(bissues) + len(bpulls) != gimport.get_issues_count() + gimport.get_pulls_count():
        print("Error: the number of Github issues and pull requests seems to be wrong.")


def check(bexport, gimport, args):
    # Retrieve data
    bissues = bexport.get_issues()
    bpulls = bexport.get_pulls()
    gissues_count = gimport.get_issues_count()
    gpulls_count = gimport.get_pulls_count()
    print("Number of bitbucket issues:", len(bissues))
    print("Number of bitbucket pull requests:", len(bpulls))
    print("Number of github issues: {}".format(gissues_count))
    print("Number of github pull requests: {}".format(gpulls_count))

    if gissues_count != 0:
        print("Warning: the github repository has existing issues, so the migration can't preserve the creation date of issues and pull requests.")
    if gissues_count + gpulls_count > len(bissues) + len(bpulls):
        print("Error: the github repository has {} issues and pull requests, but the maximum should be {} because the bitbucket repository only has {} issues and {} pull requests.".format(
            gissues_count,
            len(bissues) + len(bpulls),
            len(bissues),
            len(bpulls)
        ))

    brepo_full_name = bexport.get_repo_full_name()
    grepo_full_name = gimport.get_repo_full_name()
    if brepo_full_name not in config.KNOWN_ISSUES_COUNT_MAPPING:
        print("Error: bitbucket repository '{}' is not configured in KNOWN_ISSUES_COUNT_MAPPING.".format(brepo_full_name))
    if brepo_full_name not in config.KNOWN_REPO_MAPPING:
        print("Error: bitbucket repository '{}' is not configured in KNOWN_REPO_MAPPING.".format(brepo_full_name))

    if config.KNOWN_ISSUES_COUNT_MAPPING[brepo_full_name] != len(bissues):
        print("Error: bitbucket repository '{}' in KNOWN_ISSUES_COUNT_MAPPING maps to '{}', but the actual number of issues is '{}'.".format(
            brepo_full_name,
            config.KNOWN_ISSUES_COUNT_MAPPING[brepo_full_name],
            len(bissues)
        ))
    if config.KNOWN_REPO_MAPPING[brepo_full_name] != grepo_full_name:
        print("Error: bitbucket repository '{}' in KNOWN_REPO_MAPPING maps to '{}', but the github repository passed by command line is '{}'.".format(
            brepo_full_name,
            config.KNOWN_REPO_MAPPING[brepo_full_name],
            grepo_full_name
        ))

    # Check authors
    bnicknames = set()
    for bissue in bissues:
        bissue_id = bissue["id"]
        if bissue_id % 10 == 0:
            print("Checking bitbucket issue #{}...".format(bissue_id))
        if bissue["assignee"] is not None:
            bnicknames.add(bissue["assignee"]["nickname"])
        for bcomment in bexport.get_issue_comments(bissue_id).values():
            bnicknames.add(bcomment["user"]["nickname"])
    for bpull in bpulls:
        bpull_id = bpull["id"]
        if bpull_id % 10 == 0:
            print("Checking bitbucket pull request #{}...".format(bpull_id))
        if bpull["author"] is not None:
            bnicknames.add(bpull["author"]["nickname"])
        for bparticipant in bpull["participants"]:
            bnicknames.add(bparticipant["user"]["nickname"])
        for breviewer in bpull["reviewers"]:
            bnicknames.add(breviewer["nickname"])
        for bcomment in bexport.get_issue_comments(bissue_id).values():
            bnicknames.add(bcomment["user"]["nickname"])
        if (bpull["source"]["repository"] is None) != (bpull["source"]["commit"] is None):
            print("Info: source repository is '{}', but commit is '{}'".format(
                bpull["source"]["repository"],
                bpull["source"]["commit"]
            ))
        if (bpull["destination"]["repository"] is None) != (bpull["destination"]["commit"] is None):
            print("Info: destination repository is '{}', but commit is '{}'".format(
                bpull["destination"]["repository"],
                bpull["destination"]["commit"]
            ))
        if bpull["destination"]["repository"] is None:
            print("Info: destination repository is None")
        if bpull["source"]["branch"] is None:
            print("Info: source branch is None")
        if bpull["destination"]["branch"] is None:
            print("Info: destination branch is None")
    for nickname in bnicknames:
        if nickname not in config.USER_MAPPING:
            print("Warning: bitbucket user '{}' is not configured in USER_MAPPING.".format(nickname))


def create_parser():
    parser = argparse.ArgumentParser(
        prog="migrate",
        description="Migrate Bitbucket issues and pull requests to Github"
    )
    parser.add_argument(
        "-t", "--github-access-token",
        help="Github Access Token",
        required=True
    )
    parser.add_argument(
        "-b", "--bitbucket-repository",
        help="Full name of the Bitbucket repository (e.g. viperproject/silver)",
        required=True
    )
    parser.add_argument(
        "-g", "--github-repository",
        help="Full name of the Github repository (e.g. viperproject/silver)",
        required=True
    )
    parser.add_argument(
        "-c", "--commit-map",
        help="Path to the folder containing the mapping of mercurial commits to git commits.",
        required=True
    )
    parser.add_argument(
        "--skip-attachments",
        help="Skip the migration of attachments (development only)",
        action='store_true'
    )
    parser.add_argument(
        "--dev",
        help="Do something special (development only)"
    )
    parser.add_argument(
        "--check",
        help="Check the configuration",
        action='store_true'
    )
    return parser


def main():
    parser = create_parser()
    args = parser.parse_args()
    bexport = BitbucketExport(args.bitbucket_repository)
    gimport = GithubImport(args.github_access_token, args.github_repository, debug=False)
    cmap = CommitMap(args.commit_map)
    print("Load mapping of mercurial commits to git...")
    cmap.load_from_disk()
    if args.dev is not None:
        number = int(args.dev)
        bpull = bexport.get_pull(number)
        existing_gpulls = gimport.get_pulls()
        issue_or_pull = construct_gissue_or_gpull_from_bpull(bpull, bexport, cmap, args)
        assert issue_or_pull["type"] == "pull"
        data = issue_or_pull["data"]
        if number in existing_gpulls:
            print("Update github pull request #{}...".format(number))
            gimport.update_pull_with_comments(existing_gpulls[number], data)
        else:
            print("Create github pull request #{}...".format(number))
            gimport.create_pull_with_comments(data)
        return
    if args.check:
        check(bexport=bexport, gimport=gimport, args=args)
    else:
        bitbucket_to_github(bexport=bexport, gimport=gimport, cmap=cmap, args=args)

if __name__ == "__main__":
    main()
